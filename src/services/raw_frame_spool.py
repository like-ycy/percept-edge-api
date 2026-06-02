"""原始采集帧顺序落盘服务"""

from __future__ import annotations

import json
import os
import queue
import struct
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from loguru import logger

from src.models.database import now_shanghai
from src.services.ffmpeg_recorder import FFmpegRecorder

_RECORD_MAGIC = b"PFRM"
_RECORD_VERSION = 1
_HEADER_STRUCT = struct.Struct("<4sH I Q")
_SENTINEL = object()


@dataclass
class SpoolManifest:
    """raw spool manifest"""

    schema_version: int = 1
    created_at: str = ""
    start_time: str = ""
    end_time: str | None = None
    fps: int = 30
    segment_size_bytes: int = 0
    record_id: int | None = None
    filename_prefix: str | None = None
    metadata_snapshot: dict[str, Any] = field(default_factory=dict)
    collector_name: str = ""
    task_name: str = ""
    robot_name: str = ""
    capture_mode: str = "standard"
    raw_frame_count: int = 0
    dropped_frame_count: int = 0
    raw_bytes: int = 0
    segments: list[str] = field(default_factory=list)


@dataclass
class SpoolSealResult:
    """seal 结果"""

    capture_dir: Path
    manifest_path: Path
    sealed_at: datetime
    raw_frame_count: int
    dropped_frame_count: int
    raw_bytes: int
    segment_files: list[Path]


class _SpoolSegmentWriter:
    """单线程顺序写 segment"""

    def __init__(self, capture_dir: Path, segment_size_bytes: int) -> None:
        self._capture_dir = capture_dir
        self._segment_size_bytes = segment_size_bytes
        self._segment_index = 0
        self._current_file = None
        self._current_path: Path | None = None
        self._current_size = 0
        self._segment_paths: list[Path] = []

    @property
    def segment_paths(self) -> list[Path]:
        return list(self._segment_paths)

    def write_record(self, payload: bytes, recv_ts_ns: int) -> int:
        record_size = _HEADER_STRUCT.size + len(payload)
        if self._current_file is None or (
            self._current_size > 0 and self._current_size + record_size > self._segment_size_bytes
        ):
            self._rollover()

        assert self._current_file is not None
        self._current_file.write(
            _HEADER_STRUCT.pack(_RECORD_MAGIC, _RECORD_VERSION, len(payload), recv_ts_ns)
        )
        self._current_file.write(payload)
        self._current_size += record_size
        return record_size

    def flush(self) -> None:
        if self._current_file is None:
            return
        self._current_file.flush()
        os.fsync(self._current_file.fileno())

    def close(self) -> None:
        if self._current_file is None:
            return
        self.flush()
        self._current_file.close()
        self._current_file = None
        self._current_path = None
        self._current_size = 0

    def _rollover(self) -> None:
        self.close()
        self._segment_index += 1
        path = self._capture_dir / f"segment-{self._segment_index:06d}.bin"
        self._current_file = open(path, "ab")
        self._current_path = path
        self._current_size = path.stat().st_size if path.exists() else 0
        if path not in self._segment_paths:
            self._segment_paths.append(path)


class RawFrameSpoolWriter:
    """使用专用线程将原始 msgpack 数据顺序写入 .capture 目录"""

    def __init__(
        self,
        *,
        output_dir: Path,
        start_time: datetime,
        fps: int,
        record_id: int,
        metadata_snapshot: dict[str, Any],
        collector_name: str,
        task_name: str,
        robot_name: str,
        capture_mode: str = "standard",
        filename_prefix: str | None = None,
        segment_size_bytes: int = 256 * 1024 * 1024,
        max_queue_frames: int = 1024,
        max_queue_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.output_dir = output_dir
        self.capture_dir = output_dir / ".capture"
        self.manifest_path = self.capture_dir / "manifest.json"
        self.sealed_path = self.capture_dir / "SEALED"
        resolved_filename_prefix = filename_prefix or self._build_filename_prefix(start_time)
        self._manifest = SpoolManifest(
            created_at=now_shanghai().isoformat(),
            start_time=start_time.isoformat(),
            fps=fps,
            segment_size_bytes=segment_size_bytes,
            record_id=record_id,
            filename_prefix=resolved_filename_prefix,
            metadata_snapshot=metadata_snapshot,
            collector_name=collector_name,
            task_name=task_name,
            robot_name=robot_name,
            capture_mode=capture_mode,
        )
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max_queue_frames)
        self._lock = threading.Lock()
        self._writer_thread: threading.Thread | None = None
        self._segment_writer = _SpoolSegmentWriter(self.capture_dir, segment_size_bytes)
        self._accepting = False
        self._started = False
        self._aborted = False
        self._sealed_result: SpoolSealResult | None = None
        self._writer_error: Exception | None = None
        self._queued_bytes = 0
        self._queued_frames = 0
        self._written_bytes = 0
        self._written_frames = 0
        self._dropped_frames = 0
        self._max_queue_bytes = max_queue_bytes
        self._drained_event = threading.Event()
        self._drained_event.set()

    @property
    def queued_frames(self) -> int:
        with self._lock:
            return self._queued_frames

    @property
    def queued_bytes(self) -> int:
        with self._lock:
            return self._queued_bytes

    @property
    def written_bytes(self) -> int:
        with self._lock:
            return self._written_bytes

    @property
    def written_frames(self) -> int:
        with self._lock:
            return self._written_frames

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    def start(self) -> None:
        if self._started:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest()
        self._accepting = True
        self._started = True
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def submit(self, payload: bytes, recv_ts_ns: int) -> bool:
        if not self._accepting or self._aborted:
            return False
        if self._writer_error is not None:
            raise RuntimeError("raw spool writer 已失败") from self._writer_error

        payload_size = len(payload)
        drop_log_context: tuple[int | None, str, int, int, int, int, int, int] | None = None
        should_drop = False
        with self._lock:
            if self._queued_bytes + payload_size > self._max_queue_bytes:
                should_drop = True
                drop_log_context = self._record_drop_locked(
                    reason="queued_bytes_exceeded",
                    payload_size=payload_size,
                )
            elif self._queue.full():
                should_drop = True
                drop_log_context = self._record_drop_locked(
                    reason="queue_full",
                    payload_size=payload_size,
                )

            if not should_drop:
                self._queued_bytes += payload_size
                self._queued_frames += 1
                self._drained_event.clear()

        if should_drop:
            if drop_log_context is not None:
                self._log_dropped_frame(drop_log_context)
            return False

        try:
            self._queue.put_nowait((payload, recv_ts_ns))
            return True
        except queue.Full:
            drop_log_context = None
            with self._lock:
                self._queued_bytes -= payload_size
                self._queued_frames -= 1
                drop_log_context = self._record_drop_locked(
                    reason="queue_full_after_check",
                    payload_size=payload_size,
                )
                if self._queued_frames == 0:
                    self._drained_event.set()
            if drop_log_context is not None:
                self._log_dropped_frame(drop_log_context)
            return False

    def stop_accepting(self) -> None:
        self._accepting = False

    def wait_until_drained(self, timeout: float | None = None) -> bool:
        drained = self._drained_event.wait(timeout=timeout)
        if self._writer_error is not None:
            raise RuntimeError("raw spool writer 写入失败") from self._writer_error
        return drained

    def seal(self, end_time: datetime | None = None) -> SpoolSealResult:
        if self._sealed_result is not None:
            return self._sealed_result
        if self._aborted:
            raise RuntimeError("raw spool writer 已中止，无法 seal")
        if not self._started:
            raise RuntimeError("raw spool writer 尚未启动")

        self.stop_accepting()
        self.wait_until_drained(timeout=30.0)
        self._queue.put(_SENTINEL)
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=30.0)
        if self._writer_error is not None:
            raise RuntimeError("raw spool writer 写入失败") from self._writer_error

        sealed_at = end_time or now_shanghai()
        written_frames = self.written_frames
        dropped_frames = self.dropped_frames
        written_bytes = self.written_bytes
        self._manifest.end_time = sealed_at.isoformat()
        self._manifest.raw_frame_count = written_frames
        self._manifest.dropped_frame_count = dropped_frames
        self._manifest.raw_bytes = written_bytes
        self._manifest.segments = [path.name for path in self._segment_writer.segment_paths]
        self._write_manifest()
        self.sealed_path.write_text(sealed_at.isoformat(), encoding="utf-8")

        self._sealed_result = SpoolSealResult(
            capture_dir=self.capture_dir,
            manifest_path=self.manifest_path,
            sealed_at=sealed_at,
            raw_frame_count=written_frames,
            dropped_frame_count=dropped_frames,
            raw_bytes=written_bytes,
            segment_files=self._segment_writer.segment_paths,
        )
        logger.info(
            "raw spool 已 seal: record_id={}, frames={}, dropped_frames={}, bytes={}",
            self._manifest.record_id,
            written_frames,
            dropped_frames,
            written_bytes,
        )
        if dropped_frames > 0:
            total_frames = written_frames + dropped_frames
            drop_rate = dropped_frames / total_frames if total_frames > 0 else 0.0
            logger.warning(
                "raw spool 采集阶段存在丢帧: record_id={}, written_frames={}, "
                "dropped_frames={}, drop_rate={:.1%}",
                self._manifest.record_id,
                written_frames,
                dropped_frames,
                drop_rate,
            )
        return self._sealed_result

    def abort(self) -> None:
        if self._aborted:
            return
        self._aborted = True
        self._accepting = False
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        if self._writer_thread is not None and self._writer_thread.is_alive():
            try:
                self._queue.put_nowait(_SENTINEL)
            except queue.Full:  # pragma: no cover - 理论上前面已清空
                pass
            self._writer_thread.join(timeout=5.0)
        self._segment_writer.close()
        self._drained_event.set()

    def _writer_loop(self) -> None:
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is _SENTINEL:
                        return
                    payload, recv_ts_ns = cast(tuple[bytes, int], item)
                    assert isinstance(payload, bytes)
                    assert isinstance(recv_ts_ns, int)
                    bytes_written = self._segment_writer.write_record(payload, recv_ts_ns)
                    with self._lock:
                        self._queued_frames -= 1
                        self._queued_bytes -= len(payload)
                        self._written_frames += 1
                        self._written_bytes += bytes_written
                        if self._queued_frames == 0:
                            self._drained_event.set()
                finally:
                    self._queue.task_done()
        except Exception as exc:  # pragma: no cover - 极端故障保护
            self._writer_error = exc
            self._drained_event.set()
            logger.exception("raw spool writer 线程异常退出")
        finally:
            self._segment_writer.close()

    def _record_drop_locked(
        self,
        *,
        reason: str,
        payload_size: int,
    ) -> tuple[int | None, str, int, int, int, int, int, int] | None:
        self._dropped_frames += 1
        if self._dropped_frames != 1 and self._dropped_frames % 100 != 0:
            return None
        return (
            self._manifest.record_id,
            reason,
            self._dropped_frames,
            self._queued_frames,
            self._queued_bytes,
            payload_size,
            self._max_queue_bytes,
            self._queue.maxsize,
        )

    @staticmethod
    def _log_dropped_frame(
        context: tuple[int | None, str, int, int, int, int, int, int],
    ) -> None:
        (
            record_id,
            reason,
            dropped_frames,
            queued_frames,
            queued_bytes,
            payload_size,
            max_queue_bytes,
            queue_capacity,
        ) = context
        logger.warning(
            "raw spool 丢帧: record_id={}, reason={}, dropped_frames={}, queued_frames={}, "
            "queued_bytes={}, payload_bytes={}, max_queue_bytes={}, queue_capacity={}",
            record_id,
            reason,
            dropped_frames,
            queued_frames,
            queued_bytes,
            payload_size,
            max_queue_bytes,
            queue_capacity,
        )

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(asdict(self._manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _build_filename_prefix(start_time: datetime) -> str:
        return FFmpegRecorder.build_default_filename_prefix(start_time)
