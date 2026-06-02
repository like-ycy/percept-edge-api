"""采集 raw spool 后台整理服务"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.core.path_validator import PathValidationError, validate_safe_path
from libs.contracts.schema.episode_dataclass import Metadata
from src.models.database import CollectionRecord, Task
from src.schemas.collection import CollectionRecordStatusEnum
from src.services.collection_record_store import CollectionRecordStore
from src.services.ffmpeg_recorder import FFmpegRecorder
from src.services.raw_frame_spool import _HEADER_STRUCT, _RECORD_MAGIC
from src.services.task_converter import deserialize_instructions
from src.services.zeromq_consumer import parse_zmq_message


@dataclass
class MaterializeContext:
    record_id: int
    output_dir: Path
    raw_capture_dir: Path
    user_name: str | None
    raw_frame_count: int
    collection_status: str
    task_instructions: list[str]


class CollectionMaterializer:
    """将 .capture 中的原始消息回放为最终采集产物"""

    _PROGRESS_UPDATE_INTERVAL = 50

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker,
        record_store: CollectionRecordStore | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._record_store = record_store or CollectionRecordStore()
        self._pending_progress_tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _validate_output_dir(output_dir: str) -> Path:
        try:
            return validate_safe_path(output_dir, allow_relative=False)
        except PathValidationError as exc:
            raise ValueError(f"output_dir 路径非法: {exc}") from exc

    @classmethod
    def _validate_capture_dir(cls, raw_capture_dir: str, output_dir: str | None) -> Path:
        allowed_base = cls._validate_output_dir(output_dir) if output_dir else None
        try:
            return validate_safe_path(
                raw_capture_dir,
                allowed_base=allowed_base,
                allow_relative=False,
            )
        except PathValidationError as exc:
            raise ValueError(f"raw spool 路径非法: {exc}") from exc

    async def materialize(self, record_id: int) -> bool:
        started_at = perf_counter()
        context = await self._load_context(record_id)
        if context is None:
            return False

        capture_dir = self._validate_capture_dir(
            str(context.raw_capture_dir), str(context.output_dir)
        )
        manifest_path = capture_dir / "manifest.json"
        sealed_path = capture_dir / "SEALED"
        if not sealed_path.exists():
            await self._mark_failed(record_id, f"raw spool 未 seal: {sealed_path}")
            return False
        if not manifest_path.exists():
            await self._mark_failed(record_id, f"manifest 不存在: {manifest_path}")
            return False

        try:
            materialize_result = await self._run_materialize(context, manifest_path)
        except Exception as exc:
            await self._mark_failed(record_id, f"{type(exc).__name__}: {exc}")
            logger.exception("采集记录整理失败: record_id={}", record_id)
            return False

        await self._drain_progress_updates()

        async with self._session_maker() as db:
            record = await db.get(CollectionRecord, record_id)
            if record is None:
                logger.error("采集记录不存在，无法写回整理结果: record_id={}", record_id)
                return False
            await self._record_store.mark_materialization_succeeded(
                db=db,
                record=record,
                frame_count=materialize_result["frame_count"],
                duration=materialize_result["duration"],
                file_size=materialize_result["file_size"],
                files=materialize_result["files"],
            )

        logger.info(
            "采集记录整理完成: record_id={}, frame_count={}, total_duration_ms={:.1f}",
            record_id,
            materialize_result["frame_count"],
            (perf_counter() - started_at) * 1000,
        )
        return True

    async def _load_context(self, record_id: int) -> MaterializeContext | None:
        async with self._session_maker() as db:
            result = await db.execute(
                select(CollectionRecord).where(CollectionRecord.id == record_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                logger.error("采集记录不存在，无法整理: record_id={}", record_id)
                return None

            if record.collection_status not in {
                CollectionRecordStatusEnum.FINALIZING.value,
                CollectionRecordStatusEnum.FINALIZE_FAILED.value,
            }:
                logger.info(
                    "采集记录状态不允许整理，跳过: record_id={}, status={}",
                    record_id,
                    record.collection_status,
                )
                return None

            if not record.output_dir or not record.raw_capture_dir:
                await self._record_store.mark_materialization_failed(
                    db=db,
                    record=record,
                    error_message="缺少 output_dir 或 raw_capture_dir，无法整理",
                )
                return None

            task_instructions: list[str] = []
            if record.task_id is not None:
                task_result = await db.execute(
                    select(Task.instructions).where(
                        Task.task_id == record.task_id,
                        Task.user_id == record.user_id,
                    )
                )
                task_instructions = deserialize_instructions(task_result.scalar_one_or_none())

            validated_output_dir = self._validate_output_dir(record.output_dir)
            validated_capture_dir = self._validate_capture_dir(
                record.raw_capture_dir,
                record.output_dir,
            )

            return MaterializeContext(
                record_id=record.id,
                output_dir=validated_output_dir,
                raw_capture_dir=validated_capture_dir,
                user_name=record.user_name,
                raw_frame_count=record.raw_frame_count,
                collection_status=record.collection_status,
                task_instructions=task_instructions,
            )

    async def _run_materialize(
        self,
        context: MaterializeContext,
        manifest_path: Path,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        logger.info("开始后台整理: record_id={}, manifest={}", context.record_id, manifest_path)
        return await asyncio.to_thread(self._materialize_sync, context, manifest_path, loop)

    def _materialize_sync(
        self,
        context: MaterializeContext,
        manifest_path: Path,
        loop: asyncio.AbstractEventLoop,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metadata_snapshot = manifest.get("metadata_snapshot") or {}
        metadata = Metadata.model_validate(metadata_snapshot) if metadata_snapshot else None
        collector_name = manifest.get("collector_name") or context.user_name or ""
        task_name = manifest.get("task_name") or None
        robot_name = manifest.get("robot_name") or ""
        start_time_raw = manifest.get("start_time")
        if not isinstance(start_time_raw, str):
            raise ValueError("manifest 缺少 start_time")
        start_time = datetime.fromisoformat(start_time_raw)
        fps = int(manifest.get("fps") or 30)
        filename_prefix = self._resolve_filename_prefix(
            manifest=manifest,
            manifest_path=manifest_path,
            start_time=start_time,
        )
        video_only = manifest.get("capture_mode") == "video_only"
        total_records = self._resolve_total_records(manifest, context.raw_frame_count)
        if video_only:
            self._cleanup_video_only_outputs(context.output_dir, filename_prefix)

        recorder = FFmpegRecorder(
            output_dir=context.output_dir,
            fps=fps,
            robot_name=robot_name,
            metadata=metadata,
            collector_name=collector_name,
            task_name=task_name,
            lang_instructions=self._build_lang_instructions(context.task_instructions),
            filename_prefix=filename_prefix,
            video_only=video_only,
        )
        logger.info("整理阶段开始准备录制器: record_id={}", context.record_id)
        recorder.start(start_time)

        processed_records = 0
        next_progress_threshold = self._PROGRESS_UPDATE_INTERVAL
        replay_started_at = perf_counter()
        for segment_name in manifest.get("segments") or []:
            segment_path = manifest_path.parent / segment_name
            processed_records = self._replay_segment(
                segment_path=segment_path,
                recorder=recorder,
                processed_records=processed_records,
            )
            if total_records > 0 and processed_records >= next_progress_threshold:
                progress = int(processed_records * 100 / total_records)
                self._update_progress_from_thread(loop, context.record_id, progress)
                next_progress_threshold = processed_records + self._PROGRESS_UPDATE_INTERVAL

        logger.info(
            "整理阶段回放完成: record_id={}, processed_records={}, replay_duration_ms={:.1f}",
            context.record_id,
            processed_records,
            (perf_counter() - replay_started_at) * 1000,
        )
        stop_started_at = perf_counter()
        recorder.stop()
        logger.info(
            "整理阶段录制器停止完成: record_id={}, stop_duration_ms={:.1f}",
            context.record_id,
            (perf_counter() - stop_started_at) * 1000,
        )
        files = self._collect_materialized_files(
            context.output_dir,
            video_only=video_only,
            filename_prefix=filename_prefix,
        )
        file_size = sum(
            Path(file_path).stat().st_size for file_path in files if Path(file_path).exists()
        )
        frame_count = recorder.step_count
        skipped_frames = max(total_records - frame_count, 0)
        drop_rate = skipped_frames / total_records if total_records > 0 else 0.0
        if skipped_frames > 0:
            logger.warning(
                "整理阶段检测到丢帧: record_id={}, raw_frames={}, recorded_frames={}, "
                "skipped_frames={}, drop_rate={:.1%}",
                context.record_id,
                total_records,
                frame_count,
                skipped_frames,
                drop_rate,
            )
        duration = (frame_count + max(fps - 1, 0)) // fps if fps > 0 else 0
        logger.info(
            "整理阶段文件收集完成: record_id={}, files={}, raw_frames={}, recorded_frames={}, "
            "skipped_frames={}, total_sync_duration_ms={:.1f}",
            context.record_id,
            len(files),
            total_records,
            frame_count,
            skipped_frames,
            (perf_counter() - started_at) * 1000,
        )
        return {
            "frame_count": frame_count,
            "duration": duration,
            "file_size": file_size,
            "files": files,
        }

    @staticmethod
    def _build_lang_instructions(task_instructions: list[str]) -> list[str] | None:
        instructions = [instruction for instruction in task_instructions if instruction.strip()]
        return instructions or None

    @staticmethod
    def _resolve_filename_prefix(
        *,
        manifest: dict[str, Any],
        manifest_path: Path,
        start_time: datetime,
    ) -> str:
        filename_prefix = manifest.get("filename_prefix")
        if isinstance(filename_prefix, str) and filename_prefix.strip():
            return filename_prefix

        generated_prefix = FFmpegRecorder.build_default_filename_prefix(start_time)
        manifest["filename_prefix"] = generated_prefix
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return generated_prefix

    def _update_progress_from_thread(
        self,
        loop: asyncio.AbstractEventLoop,
        record_id: int,
        progress: int,
    ) -> None:
        loop.call_soon_threadsafe(self._schedule_progress_update, record_id, progress)

    def _schedule_progress_update(self, record_id: int, progress: int) -> None:
        task = asyncio.create_task(self._update_progress(record_id, progress))
        self._pending_progress_tasks.add(task)
        task.add_done_callback(
            lambda finished_task: self._log_progress_update_error(
                finished_task, record_id=record_id, progress=progress
            )
        )
        task.add_done_callback(self._pending_progress_tasks.discard)

    async def _drain_progress_updates(self) -> None:
        await asyncio.sleep(0)
        if not self._pending_progress_tasks:
            return
        await asyncio.gather(*self._pending_progress_tasks, return_exceptions=True)

    @staticmethod
    def _log_progress_update_error(
        task: asyncio.Task[None],
        *,
        record_id: int,
        progress: int,
    ) -> None:
        try:
            task.result()
        except Exception:
            logger.exception(
                "整理阶段进度更新失败: record_id={}, progress={}",
                record_id,
                progress,
            )

    async def _update_progress(self, record_id: int, progress: int) -> None:
        async with self._session_maker() as db:
            record = await db.get(CollectionRecord, record_id)
            if record is None:
                return
            await self._record_store.update_materialization_progress(
                db=db,
                record=record,
                progress=progress,
            )

    async def _mark_failed(self, record_id: int, error_message: str) -> None:
        async with self._session_maker() as db:
            record = await db.get(CollectionRecord, record_id)
            if record is None:
                return
            await self._record_store.mark_materialization_failed(
                db=db,
                record=record,
                error_message=error_message,
            )

    def _replay_segment(
        self,
        *,
        segment_path: Path,
        recorder: FFmpegRecorder,
        processed_records: int,
    ) -> int:
        with open(segment_path, "rb") as file_obj:
            while True:
                header = file_obj.read(_HEADER_STRUCT.size)
                if not header:
                    break
                if len(header) != _HEADER_STRUCT.size:
                    raise ValueError(f"segment header 不完整: {segment_path}")
                magic, version, payload_len, _recv_ts_ns = _HEADER_STRUCT.unpack(header)
                if magic != _RECORD_MAGIC:
                    raise ValueError(f"segment magic 非法: {segment_path}")
                if version != 1:
                    raise ValueError(f"segment version 不支持: {version}")
                payload = file_obj.read(payload_len)
                if len(payload) != payload_len:
                    raise ValueError(f"segment payload 不完整: {segment_path}")
                self._replay_payload(payload, recorder)
                processed_records += 1
        return processed_records

    @staticmethod
    def _replay_payload(payload: bytes, recorder: FFmpegRecorder) -> None:
        frame = parse_zmq_message(payload)
        recorder.write_frame(frame)

    @staticmethod
    def _collect_materialized_files(
        output_dir: Path,
        *,
        video_only: bool = False,
        filename_prefix: str = "",
    ) -> list[str]:
        materialized_files = []
        for file in sorted(output_dir.iterdir()):
            if not file.is_file():
                continue
            if video_only:
                if (
                    file.suffix == ".mp4"
                    and file.name.startswith(f"{filename_prefix}_")
                    and file.name.endswith("_rgb.mp4")
                ):
                    materialized_files.append(str(file.absolute()))
                continue
            if file.suffix in {".mp4", ".json", ".snapshot"}:
                materialized_files.append(str(file.absolute()))
        return materialized_files

    @staticmethod
    def _cleanup_video_only_outputs(output_dir: Path, filename_prefix: str) -> None:
        stale_files = [
            *output_dir.glob(f"{filename_prefix}*.mp4"),
            output_dir / f"{filename_prefix}.json",
            output_dir / "writer_frame_counts.snapshot",
        ]
        for stale_file in stale_files:
            if stale_file.exists() and stale_file.is_file():
                stale_file.unlink()

    @staticmethod
    def _resolve_total_records(manifest: dict[str, Any], raw_frame_count: int) -> int:
        manifest_count = manifest.get("raw_frame_count")
        if isinstance(manifest_count, int) and manifest_count > 0:
            return manifest_count
        return max(raw_frame_count, 0)
