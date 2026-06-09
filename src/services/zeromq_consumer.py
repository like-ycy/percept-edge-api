"""ZeroMQ 消费者服务"""

from __future__ import annotations

import ast
import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Protocol

import msgpack
import zmq
import zmq.asyncio
from loguru import logger

from src.schemas.collection import ArmFrame, CameraFrame, ExtraComponentFrame, ZmqFrame
from src.schemas.debug import FrameMetadata, ZeroMQDebugInfo


class RawFrameSink(Protocol):
    """采集 raw sink 协议"""

    def submit(self, payload: bytes, recv_ts_ns: int) -> bool: ...

    @property
    def queued_frames(self) -> int: ...

    @property
    def queue_capacity(self) -> int: ...


CollectionSinkFailureHandler = Callable[[Exception], Awaitable[None] | None]
ArmFrameBuildTimingHandler = Callable[[str, float, int], None]


def _parse_eef_values(value) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, list | tuple):
        values = [float(item) for item in value if isinstance(item, int | float)]
        return values or None
    return None


def _parse_gripper_value(frame_data) -> list[float] | None:
    gripper = frame_data.get("gripper_data")
    if gripper is not None:
        if isinstance(gripper, list | tuple):
            return [float(item) for item in gripper if isinstance(item, int | float)]
        if isinstance(gripper, int | float):
            return [float(gripper)]
    return None


def _parse_float_list(value) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, list | tuple):
        values: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int | float):
                return None
            values.append(float(item))
        return values
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return [float(value)]


def _parse_bytes_field(value) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value if value else None
    if isinstance(value, str):
        if not value:
            return None
        if (value.startswith("b'") or value.startswith('b"')) and len(value) > 2:
            try:
                result = ast.literal_eval(value)
                return result if result else None
            except (ValueError, SyntaxError):
                pass
        return value.encode() if value else None
    return None


def _parse_tool_io_data(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def parse_zmq_message(
    raw_data: bytes,
    arm_frame_build_timing_handler: ArmFrameBuildTimingHandler | None = None,
) -> ZmqFrame:
    """解析 msgpack 消息为 ZmqFrame"""
    data = msgpack.unpackb(raw_data, raw=False)

    cameras = []
    arms = []
    extras = []

    for frame in data["frames"]:
        component_id = frame["component_id"]
        frame_data = frame["data"]
        timestamp = frame["timestamp"]

        if "color_data" in frame_data or "depth_data" in frame_data:
            color_data = _parse_bytes_field(frame_data.get("color_data"))
            depth_data = _parse_bytes_field(frame_data.get("depth_data"))
            cameras.append(
                CameraFrame(
                    component_id=component_id,
                    timestamp=timestamp,
                    color_data=color_data,
                    depth_data=depth_data,
                )
            )
        elif "joint_data" in frame_data:
            joint = frame_data["joint_data"]
            eef = frame_data.get("eef_data")

            arm_build_start_ns = time.perf_counter_ns()
            try:
                arms.append(
                    ArmFrame(
                        component_id=component_id,
                        timestamp=timestamp,
                        joint_pos=joint.get("joint_pos", []),
                        joint_vel=joint.get("joint_vel", []),
                        joint_cur=joint.get("joint_cur", []),
                        joint_eff=joint.get("joint_eff", []),
                        joint_force=joint.get("joint_force", []),
                        eef=_parse_eef_values(eef),
                        gripper=_parse_gripper_value(frame_data),
                        translation=_parse_float_list(frame_data.get("translation_data")),
                        tool_io_data=_parse_tool_io_data(frame_data.get("tool_io_data")),
                    )
                )
            finally:
                arm_build_elapsed_ns = time.perf_counter_ns() - arm_build_start_ns
                if arm_frame_build_timing_handler is not None:
                    arm_frame_build_timing_handler(
                        component_id,
                        timestamp,
                        arm_build_elapsed_ns,
                    )
        else:
            extras.append(
                ExtraComponentFrame(
                    component_id=component_id,
                    timestamp=timestamp,
                    payload=frame_data,
                )
            )

    return ZmqFrame(
        timestamp=data["timestamp"],
        cameras=cameras,
        arms=arms,
        extras=extras,
    )


class ZeroMQConsumer:
    """ZeroMQ 消费者，持续接收帧数据"""

    def __init__(
        self,
        endpoint: str,
        collection_queue_size: int = 100,
        *,
        enable_runtime_watchdog: bool = True,
        stale_threshold_seconds: float = 5.0,
        watchdog_interval_seconds: float = 1.0,
        startup_grace_seconds: float = 5.0,
    ):
        self.endpoint = endpoint
        self.context = zmq.asyncio.Context()
        self.socket = None
        self._latest_frame: ZmqFrame | None = None
        self._collection_queue_size = collection_queue_size
        self._collection_enabled = False
        self._running = False
        self._task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._pause_request = False
        self._consumer_paused = asyncio.Event()
        self._collection_raw_sink: RawFrameSink | None = None
        self._collection_sink_failure_handler: CollectionSinkFailureHandler | None = None
        self._collection_sink_failed = False
        self._enable_runtime_watchdog = enable_runtime_watchdog
        self._stale_threshold_seconds = stale_threshold_seconds
        self._watchdog_interval_seconds = watchdog_interval_seconds
        self._startup_grace_seconds = startup_grace_seconds
        self._stream_stale_logged = False

        self._frames_received = 0
        self._frames_processed = 0
        self._frames_dropped = 0
        self._frames_flushed = 0
        self._last_receive_time: datetime | None = None
        self._start_time: datetime | None = None
        self._latest_frame_metadata: FrameMetadata | None = None
        self._latest_parse_time_ms = 0.0
        self._latest_frame_delay_ms = 0.0
        self._collection_arm_frame_build_count = 0
        self._collection_arm_frame_build_total_ns = 0

    async def start(self) -> None:
        self.socket = self.context.socket(zmq.SUB)
        self.socket.setsockopt(zmq.RCVHWM, 10)
        self.socket.connect(self.endpoint)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "")
        self._latest_frame = None
        self._last_receive_time = None
        self._latest_frame_metadata = None
        self._latest_parse_time_ms = 0.0
        self._latest_frame_delay_ms = 0.0
        self._frames_received = 0
        self._frames_processed = 0
        self._frames_dropped = 0
        self._frames_flushed = 0
        self._running = True
        self._start_time = datetime.now(timezone.utc)
        self._stream_stale_logged = False
        self._task = asyncio.create_task(self._consume_loop())
        if self._enable_runtime_watchdog:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        if self.socket:
            self.socket.close()

    async def enable_collection_queue(self) -> None:
        self._pause_request = True
        await self._consumer_paused.wait()

        max_flush = 100
        flushed_count = 0
        if self.socket:
            while flushed_count < max_flush:
                events = await self.socket.poll(timeout=0)
                if events == 0:
                    break
                await self.socket.recv()
                flushed_count += 1

        self._frames_flushed = flushed_count
        self._latest_frame = None
        self._reset_collection_arm_frame_build_stats()
        self._collection_enabled = True
        self._collection_sink_failed = False
        self._consumer_paused.clear()
        self._pause_request = False

    def disable_collection_queue(self) -> None:
        was_collection_enabled = self._collection_enabled
        self._collection_enabled = False
        self._collection_sink_failed = False
        if was_collection_enabled:
            self._log_collection_arm_frame_build_summary("采集结束")

    def attach_collection_sink(self, sink: RawFrameSink) -> None:
        self._collection_raw_sink = sink
        self._collection_sink_failed = False

    def detach_collection_sink(self) -> None:
        self._collection_raw_sink = None
        self._collection_sink_failed = False

    def set_collection_sink_failure_handler(self, handler: CollectionSinkFailureHandler) -> None:
        self._collection_sink_failure_handler = handler

    async def _consume_loop(self) -> None:
        while self._running:
            if self._pause_request:
                self._consumer_paused.set()
                await asyncio.sleep(0.005)
                continue

            if self.socket is None:
                await asyncio.sleep(0.01)
                continue

            try:
                msg = await self.socket.recv()
                self._frames_received += 1
                self._last_receive_time = datetime.now(timezone.utc)

                if self._collection_enabled and self._collection_raw_sink is not None:
                    try:
                        if not self._collection_raw_sink.submit(msg, time.time_ns()):
                            self._frames_dropped += 1
                    except RuntimeError as exc:
                        self._frames_dropped += 1
                        await self._handle_collection_sink_failure(exc)
                        continue

                frame = self._parse_zmq_message(msg)
                self._frames_processed += 1
                self._latest_frame_metadata = self._extract_frame_metadata(frame)
                self._latest_frame = frame

            except asyncio.CancelledError:
                break
            except zmq.Again:
                continue
            except Exception as e:
                logger.warning(f"ZeroMQ 帧处理异常: {type(e).__name__}: {e}")
                continue

    async def _watchdog_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._watchdog_interval_seconds)
                self._check_stream_watchdog(datetime.now(timezone.utc))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("ZeroMQ watchdog 检查异常: {}: {}", type(exc).__name__, exc)

    def _check_stream_watchdog(self, now: datetime) -> None:
        if not self._enable_runtime_watchdog or not self._running or self.socket is None:
            return

        if self._pause_request:
            return

        if self._start_time is None:
            return

        startup_elapsed = (now - self._start_time).total_seconds()
        if startup_elapsed < self._startup_grace_seconds:
            return

        if self._last_receive_time is None:
            if not self._stream_stale_logged:
                logger.error(
                    "ZeroMQ collection 启动后未收到任何数据: endpoint={}, grace={:.1f}s",
                    self.endpoint,
                    self._startup_grace_seconds,
                )
                self._stream_stale_logged = True
            return

        elapsed = (now - self._last_receive_time).total_seconds()
        if elapsed > self._stale_threshold_seconds:
            if not self._stream_stale_logged:
                logger.error(
                    "ZeroMQ collection 数据流中断: endpoint={}, elapsed={:.1f}s, threshold={:.1f}s",
                    self.endpoint,
                    elapsed,
                    self._stale_threshold_seconds,
                )
                self._stream_stale_logged = True
            return

        if self._stream_stale_logged:
            logger.info(
                "ZeroMQ collection 数据流已恢复: endpoint={}, last_receive_time={}",
                self.endpoint,
                self._last_receive_time,
            )
            self._stream_stale_logged = False

    async def _handle_collection_sink_failure(self, exc: Exception) -> None:
        if self._collection_sink_failed:
            return
        self._collection_sink_failed = True
        self._collection_enabled = False
        self._collection_raw_sink = None
        self._log_collection_arm_frame_build_summary("采集 raw sink 失效")
        logger.error("采集 raw sink 已失效: {}", exc)

        if self._collection_sink_failure_handler is None:
            return

        result = self._collection_sink_failure_handler(exc)
        if inspect.isawaitable(result):
            await result

    def _parse_zmq_message(self, raw_data: bytes) -> ZmqFrame:
        start_time = time.perf_counter()
        timing_handler = self._record_collection_arm_frame_build_time
        zmq_frame = parse_zmq_message(raw_data, timing_handler)
        self._latest_parse_time_ms = (time.perf_counter() - start_time) * 1000
        current_ts = datetime.now(timezone.utc).timestamp()
        self._latest_frame_delay_ms = (current_ts - zmq_frame.timestamp) * 1000
        return zmq_frame

    def _reset_collection_arm_frame_build_stats(self) -> None:
        self._collection_arm_frame_build_count = 0
        self._collection_arm_frame_build_total_ns = 0

    def _record_collection_arm_frame_build_time(
        self,
        component_id: str,
        frame_timestamp: float,
        elapsed_ns: int,
    ) -> None:
        if not self._collection_enabled:
            return

        self._collection_arm_frame_build_count += 1
        self._collection_arm_frame_build_total_ns += elapsed_ns

    def _log_collection_arm_frame_build_summary(self, reason: str) -> None:
        count = self._collection_arm_frame_build_count
        if count == 0:
            logger.info("{}，本次采集未构建 ArmFrame 对象", reason)
            return

        total_ms = self._collection_arm_frame_build_total_ns / 1_000_000
        avg_ms = total_ms / count
        logger.info(
            "{}，本次采集 ArmFrame 对象构建统计: count={}, total_ms={:.3f}, avg_per_frame_ms={:.3f}",
            reason,
            count,
            total_ms,
            avg_ms,
        )

    def get_latest_frame(self) -> ZmqFrame | None:
        return self._latest_frame

    def has_recent_collection_data(self, max_age_seconds: float = 1.0) -> bool:
        if self._latest_frame is None or self._last_receive_time is None:
            return False
        age_seconds = (datetime.now(timezone.utc) - self._last_receive_time).total_seconds()
        return age_seconds <= max_age_seconds

    async def wait_until_collection_ready(
        self, timeout: float = 5.0, check_interval: float = 0.05
    ) -> bool:
        if self._latest_frame is not None and self._last_receive_time is not None:
            return True

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._latest_frame is not None and self._last_receive_time is not None:
                return True
            await asyncio.sleep(min(check_interval, max(deadline - loop.time(), 0.0)))
        return self._latest_frame is not None and self._last_receive_time is not None

    def is_collection_enabled(self) -> bool:
        return self._collection_enabled

    def _extract_frame_metadata(self, frame: ZmqFrame) -> FrameMetadata:
        return FrameMetadata(
            timestamp=datetime.fromtimestamp(frame.timestamp, tz=timezone.utc),
            has_image=len(frame.cameras) > 0,
            image_shape=None,
            image_dtype="jpeg",
            has_robot_state=len(frame.arms) > 0,
            robot_state_fields=[a.component_id for a in frame.arms],
        )

    def get_debug_info(self) -> ZeroMQDebugInfo:
        now = datetime.now(timezone.utc)
        uptime = 0.0
        if self._start_time:
            uptime = (now - self._start_time).total_seconds()

        queue_size = self._collection_raw_sink.queued_frames if self._collection_raw_sink else 0
        queue_capacity = (
            self._collection_raw_sink.queue_capacity
            if self._collection_raw_sink is not None
            else self._collection_queue_size
        )

        fps = round(self._frames_received / uptime, 2) if uptime > 0 else 0.0
        stale_seconds = None
        if self._last_receive_time is not None:
            stale_seconds = (now - self._last_receive_time).total_seconds()

        is_stale = False
        if self._enable_runtime_watchdog and self._start_time is not None:
            startup_elapsed = (now - self._start_time).total_seconds()
            if startup_elapsed >= self._startup_grace_seconds:
                if self._last_receive_time is None:
                    is_stale = True
                elif stale_seconds is not None:
                    is_stale = stale_seconds > self._stale_threshold_seconds

        return ZeroMQDebugInfo(
            is_connected=self._running and self.socket is not None,
            endpoint=self.endpoint,
            frames_received=self._frames_received,
            frames_processed=self._frames_processed,
            frames_dropped=self._frames_dropped,
            frames_flushed=self._frames_flushed,
            last_receive_time=self._last_receive_time,
            queue_size=queue_size,
            queue_capacity=queue_capacity,
            latest_frame=self._latest_frame_metadata,
            uptime_seconds=uptime,
            fps=fps,
            watchdog_enabled=self._enable_runtime_watchdog,
            is_stale=is_stale,
            stale_seconds=stale_seconds,
            stale_threshold_seconds=self._stale_threshold_seconds,
            is_collection_enabled=self._collection_enabled,
            frame_delay_ms=self._latest_frame_delay_ms,
            parse_time_ms=self._latest_parse_time_ms,
            last_frame_timestamp=self._latest_frame.timestamp if self._latest_frame else None,
        )
