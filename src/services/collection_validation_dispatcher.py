"""采集停止后的完整性校验调度"""

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger
from sqlalchemy import select

from src.models.database import CollectionRecord, now_shanghai
from src.schemas.collection import CollectionRecordStatusEnum
from src.schemas.validation import ValidationResult, ValidationStatusEnum
from src.services.collection_sync_dispatcher import CollectionSyncDispatcher
from src.services.data_integrity_validator import DataIntegrityValidator

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.core.task_manager import BackgroundTaskManager
    from src.services.cloud_client import CloudClient
    from src.services.collection_lock_service import CollectionLockService
    from src.services.monitor_service import MonitorService
    from src.services.upload_service import UploadService


class CollectionValidationDispatcher:
    """采集完整性校验后台调度器"""

    def __init__(
        self,
        *,
        task_manager: "BackgroundTaskManager | None",
        session_maker: "async_sessionmaker | None",
        monitor_service: "MonitorService | None",
        upload_service: "UploadService | None",
        cloud_client: "CloudClient | None",
        storage_path: Path,
        remote_path: str,
        lock_service: "CollectionLockService | None" = None,
        frame_drop_threshold: float = 0.10,
    ) -> None:
        self._task_manager = task_manager
        self._session_maker = session_maker
        self._monitor_service = monitor_service
        self._upload_service = upload_service
        self._cloud_client = cloud_client
        self._storage_path = storage_path
        self._remote_path = remote_path
        self._lock_service = lock_service
        self._frame_drop_threshold = frame_drop_threshold
        self._inflight_record_ids: set[int] = set()
        self._fallback_tasks: set[asyncio.Task] = set()

    def schedule(self, record_id: int, *, upload_after_success: bool = True) -> bool:
        """调度后台完整性校验任务"""
        if record_id in self._inflight_record_ids:
            logger.warning(f"采集校验任务已在执行中，跳过重复调度: record_id={record_id}")
            return False

        task_id = f"collection_validation_{record_id}_{datetime.now().timestamp()}"
        coro = self._validate_and_continue(record_id, upload_after_success=upload_after_success)
        self._inflight_record_ids.add(record_id)

        if self._task_manager:
            task = self._task_manager.create_task(task_id=task_id, coro=coro, critical=True)
            if task is None:
                self._inflight_record_ids.discard(record_id)
                return False
            logger.debug(f"采集校验任务已调度: {task_id}")
            return True

        logger.warning(f"TaskManager 未配置，采集校验任务将不受管理: {task_id}")
        task = asyncio.create_task(coro)
        self._fallback_tasks.add(task)
        task.add_done_callback(self._fallback_tasks.discard)
        return True

    async def _validate_and_continue(
        self,
        record_id: int,
        *,
        upload_after_success: bool,
    ) -> None:
        """执行校验并在成功后调度上传"""
        try:
            if not self._session_maker:
                logger.error(f"未配置数据库会话工厂，无法执行采集校验: record_id={record_id}")
                return

            async with self._session_maker() as db:
                result = await db.execute(
                    select(CollectionRecord).where(CollectionRecord.id == record_id)
                )
                record = result.scalar_one_or_none()
                if not record:
                    logger.error(f"采集记录不存在，无法执行校验: record_id={record_id}")
                    return

                if record.collection_status != CollectionRecordStatusEnum.VALIDATING.value:
                    logger.info(
                        "采集记录当前状态不是 validating，跳过校验: record_id={}, status={}",
                        record_id,
                        record.collection_status,
                    )
                    return

                validation_result = await self._validate_record(record)
                record.validation_status = validation_result.status.value
                record.validation_summary = validation_result.summary
                record.validation_result = validation_result.model_dump_json()
                record.validated_at = now_shanghai()
                record.collection_status = (
                    CollectionRecordStatusEnum.COMPLETED.value
                    if validation_result.status == ValidationStatusEnum.SUCCESS
                    else CollectionRecordStatusEnum.VALIDATION_FAILED.value
                )
                await db.commit()
                await db.refresh(record)

            logger.info(
                "采集数据后台校验完成: record_id={}, status={}, summary={}",
                record_id,
                validation_result.status,
                validation_result.summary,
            )

            if validation_result.status != ValidationStatusEnum.SUCCESS:
                logger.warning(
                    "数据完整性校验失败 (record_id={}): {}",
                    record_id,
                    validation_result.summary,
                )
                logger.warning(
                    "校验错误详情: {}",
                    [error.message for error in validation_result.errors],
                )
                if (
                    validation_result.status == ValidationStatusEnum.FAILED
                    and self._lock_service is not None
                ):
                    await self._lock_service.lock(
                        record_id=record_id,
                        reason=validation_result.summary,
                    )
                    logger.warning(
                        "采集全局锁已触发: record_id={}, reason={}",
                        record_id,
                        validation_result.summary,
                    )
                return

            await self._cleanup_raw_capture(record_id)
            if not upload_after_success:
                logger.info(
                    "采集数据校验通过，已跳过自动上传: record_id={}",
                    record_id,
                )
                return
            self._sync_dispatcher.schedule(record)
        finally:
            self._inflight_record_ids.discard(record_id)

    async def _cleanup_raw_capture(self, record_id: int) -> None:
        """校验通过后删除 .capture 原始 bin 目录，释放磁盘"""
        if not self._session_maker:
            return
        async with self._session_maker() as db:
            record = await db.get(CollectionRecord, record_id)
            if record is None or not record.raw_capture_dir:
                return
            capture_dir = Path(record.raw_capture_dir)
            if capture_dir.exists():
                try:
                    await asyncio.to_thread(shutil.rmtree, capture_dir)
                    logger.info(
                        "校验通过，已删除 raw spool: record_id={}, path={}",
                        record_id,
                        capture_dir,
                    )
                except Exception as exc:
                    logger.warning(
                        "删除 raw spool 失败（不影响上传）: record_id={}, path={}, error={}",
                        record_id,
                        capture_dir,
                        exc,
                    )
                    return
            record.raw_capture_dir = None
            await db.commit()

    async def _validate_record(self, record: CollectionRecord):
        """执行单条采集记录的完整性校验"""
        output_dir = Path(record.output_dir) if record.output_dir else None
        if not output_dir:
            return ValidationResult(
                status=ValidationStatusEnum.FAILED,
                directory=str(output_dir) if output_dir else "",
                expected_steps=0,
                expected_files=[],
                found_files=[],
                missing_files=[],
                extra_files=[],
                errors=[],
                summary="无法校验：输出目录不可用",
            )

        if self._is_video_only_record(record):
            validator = DataIntegrityValidator(
                self._monitor_service, frame_drop_threshold=self._frame_drop_threshold
            )
            return await validator.validate_video_only_collection(
                output_dir,
                expected_frames=self._resolve_expected_frame_count(record),
            )

        if not self._monitor_service:
            return ValidationResult(
                status=ValidationStatusEnum.FAILED,
                directory=str(output_dir),
                expected_steps=0,
                expected_files=[],
                found_files=[],
                missing_files=[],
                extra_files=[],
                errors=[],
                summary="无法校验：MonitorService 不可用",
            )

        validator = DataIntegrityValidator(
            self._monitor_service, frame_drop_threshold=self._frame_drop_threshold
        )
        return await validator.validate_collection(
            output_dir,
            expected_frames=self._resolve_expected_frame_count(record),
        )

    @staticmethod
    def _resolve_expected_frame_count(record: CollectionRecord) -> int:
        candidates = [record.frame_count, record.raw_frame_count]
        wall_clock_frames = CollectionValidationDispatcher._resolve_wall_clock_expected_frames(
            record
        )
        if wall_clock_frames is not None:
            candidates.append(wall_clock_frames)
        return max(candidates)

    @staticmethod
    def _resolve_wall_clock_expected_frames(record: CollectionRecord) -> Optional[int]:
        if record.start_time is None or record.end_time is None:
            return None

        elapsed_seconds = CollectionValidationDispatcher._elapsed_seconds(
            record.start_time,
            record.end_time,
        )
        if elapsed_seconds <= 0:
            return None

        fps = CollectionValidationDispatcher._resolve_record_fps(record)
        return max(int(elapsed_seconds * fps), 0)

    @staticmethod
    def _elapsed_seconds(start_time: datetime, end_time: datetime) -> float:
        if (start_time.tzinfo is None) != (end_time.tzinfo is None):
            start_time = start_time.replace(tzinfo=None)
            end_time = end_time.replace(tzinfo=None)
        return (end_time - start_time).total_seconds()

    @staticmethod
    def _resolve_record_fps(record: CollectionRecord) -> int:
        if not record.raw_capture_dir:
            return 30

        manifest_path = Path(record.raw_capture_dir) / "manifest.json"
        if not manifest_path.exists():
            return 30

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("读取采集 manifest 失败，按 30fps 计算期望帧数: {}", manifest_path)
            return 30

        fps = manifest.get("fps")
        if isinstance(fps, bool):
            return 30
        if isinstance(fps, int) and fps > 0:
            return fps
        return 30

    @staticmethod
    def _is_video_only_record(record: CollectionRecord) -> bool:
        if not record.raw_capture_dir:
            return False
        manifest_path = Path(record.raw_capture_dir) / "manifest.json"
        if not manifest_path.exists():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("读取采集 manifest 失败，按标准模式校验: {}", manifest_path)
            return False
        return manifest.get("capture_mode") == "video_only"

    @property
    def _sync_dispatcher(self) -> CollectionSyncDispatcher:
        return CollectionSyncDispatcher(
            task_manager=self._task_manager,
            upload_service=self._upload_service,
            session_maker=self._session_maker,
            cloud_client=self._cloud_client,
            storage_path=self._storage_path,
            remote_path=self._remote_path,
        )
