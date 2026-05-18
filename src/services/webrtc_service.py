# src/services/webrtc_service.py
"""WebRTC 推流服务"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import cv2
import numpy as np
from aiortc import RTCPeerConnection, RTCRtpSender, VideoStreamTrack
from av import VideoFrame

from src.config import WebrtcPreviewSettings, WebrtcSettings
from src.schemas.debug import (
    WebRTCConnectionDebugInfo,
    WebRTCDebugInfo,
    WebRTCPreviewSettingsDebug,
    WebRTCTrackDebugInfo,
)
from src.services.zeromq_consumer import ZeroMQConsumer


@dataclass
class WebRTCConnectionMeta:
    """WebRTC 连接元数据。"""

    camera_id: str
    created_at: datetime


class CameraVideoTrack(VideoStreamTrack):
    """单个摄像头的视频轨道"""

    def __init__(
        self,
        consumer: ZeroMQConsumer,
        camera_id: str,
        preview_settings: WebrtcPreviewSettings | None = None,
        time_provider: Callable[[], float] | None = None,
    ):
        """初始化视频轨道

        Args:
            consumer: ZeroMQ 消费者实例
            camera_id: 摄像头 ID，如 "camera1"
        """
        super().__init__()
        self.consumer = consumer
        self.camera_id = camera_id
        self.preview_settings = preview_settings or WebrtcPreviewSettings()
        self._time_provider = time_provider or time.monotonic
        self._last_preview_image: np.ndarray | None = None
        self._last_preview_at = 0.0
        self._adaptive_profile_index = 0
        self._last_adaptive_check_at = 0.0
        self._last_profile_change_at = 0.0
        self._metrics = WebRTCTrackDebugInfo(active_profile=self._active_profile_name())

    async def recv(self) -> VideoFrame:
        """接收下一帧

        Returns:
            VideoFrame 对象
        """
        recv_started_at = self._time_provider()
        self._metrics.frames_requested += 1
        pts, time_base = await self.next_timestamp()
        if self._should_reuse_last_preview():
            last_preview_image = self._last_preview_image
            if last_preview_image is not None:
                self._metrics.frames_reused += 1
                return self._finish_frame(last_preview_image, pts, time_base, recv_started_at)

        now = self._time_provider()
        frame_data = self.consumer.get_latest_frame()

        img = None
        if frame_data is not None:
            # 查找指定摄像头的 RGB 数据
            for camera in frame_data.cameras:
                if camera.component_id == self.camera_id and camera.color_data:
                    self._metrics.last_encoded_bytes = len(camera.color_data)
                    if len(camera.color_data) > self.preview_settings.max_encoded_frame_bytes:
                        self._metrics.oversized_frames_skipped += 1
                        break
                    decode_started_at = self._time_provider()
                    img = cv2.imdecode(
                        np.frombuffer(camera.color_data, dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    self._metrics.last_decode_ms = (
                        self._time_provider() - decode_started_at
                    ) * 1000
                    if img is not None:
                        source_height, source_width = img.shape[:2]
                        self._metrics.last_source_width = source_width
                        self._metrics.last_source_height = source_height
                        img = self._resize_for_preview(img)
                        # BGR -> RGB
                        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    else:
                        self._metrics.decode_failures += 1
                    break

        if img is None:
            # 无帧时返回黑色图像
            self._metrics.fallback_frames += 1
            img = np.zeros(
                (
                    self.preview_settings.fallback_height,
                    self.preview_settings.fallback_width,
                    3,
                ),
                dtype=np.uint8,
            )

        self._last_preview_image = img
        self._last_preview_at = now
        return self._finish_frame(img, pts, time_base, recv_started_at)

    def _should_reuse_last_preview(self) -> bool:
        if not self.preview_settings.enabled or self._last_preview_image is None:
            return False
        min_interval = 1.0 / self.preview_settings.max_fps
        return self._time_provider() - self._last_preview_at < min_interval

    def _resize_for_preview(self, img: np.ndarray) -> np.ndarray:
        if not self.preview_settings.enabled:
            return img

        height, width = img.shape[:2]
        limits = self._current_preview_limits()
        scale = min(
            1.0,
            limits.max_width / width,
            limits.max_height / height,
        )
        if scale >= 1.0:
            self._metrics.last_output_width = width
            self._metrics.last_output_height = height
            self._metrics.last_resize_ms = 0.0
            return img

        target_width = self._even_dimension(round(width * scale))
        target_height = self._even_dimension(round(height * scale))
        resize_started_at = self._time_provider()
        resized = cv2.resize(img, (target_width, target_height), interpolation=cv2.INTER_AREA)
        self._metrics.resize_count += 1
        self._metrics.last_resize_ms = (self._time_provider() - resize_started_at) * 1000
        self._metrics.last_output_width = target_width
        self._metrics.last_output_height = target_height
        return resized

    def _current_preview_limits(self):
        adaptive = self.preview_settings.adaptive
        if adaptive.enabled and adaptive.profiles:
            return adaptive.profiles[self._adaptive_profile_index]
        return self.preview_settings

    def _active_profile_name(self) -> str | None:
        adaptive = self.preview_settings.adaptive
        if adaptive.enabled and adaptive.profiles:
            return adaptive.profiles[self._adaptive_profile_index].name
        return None

    def _maybe_degrade_before_frame(self) -> None:
        self._maybe_degrade_profile(self._time_provider())

    def _maybe_degrade_profile(self, now: float) -> None:
        adaptive = self.preview_settings.adaptive
        if not adaptive.enabled or len(adaptive.profiles) < 2:
            return
        if self._adaptive_profile_index >= len(adaptive.profiles) - 1:
            return
        if now - self._last_adaptive_check_at < adaptive.check_interval_seconds:
            return
        self._last_adaptive_check_at = now
        if now - self._last_profile_change_at < adaptive.cooldown_seconds:
            return

        recv_ms = self._metrics.last_total_recv_ms
        decode_ms = self._metrics.last_decode_ms
        should_degrade = (recv_ms is not None and recv_ms >= adaptive.recv_ms_high) or (
            decode_ms is not None and decode_ms >= adaptive.decode_ms_high
        )
        if not should_degrade:
            return

        self._adaptive_profile_index += 1
        self._last_profile_change_at = now
        self._metrics.active_profile = self._active_profile_name()

    def _finish_frame(
        self, img: np.ndarray, pts: int, time_base, recv_started_at: float
    ) -> VideoFrame:
        height, width = img.shape[:2]
        self._metrics.last_output_width = width
        self._metrics.last_output_height = height
        self._metrics.frames_sent += 1
        self._metrics.last_frame_at = datetime.now(timezone.utc)
        self._metrics.last_total_recv_ms = (self._time_provider() - recv_started_at) * 1000
        self._maybe_degrade_profile(self._time_provider())
        return self._build_frame(img, pts, time_base)

    def get_debug_info(self) -> WebRTCTrackDebugInfo:
        return self._metrics.model_copy(deep=True)

    @staticmethod
    def _even_dimension(value: int) -> int:
        return max(2, value - value % 2)

    @staticmethod
    def _build_frame(img: np.ndarray, pts: int, time_base) -> VideoFrame:
        frame = VideoFrame.from_ndarray(img, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


class WebRTCService:
    """WebRTC 推流服务"""

    def __init__(self, consumer: ZeroMQConsumer, settings: WebrtcSettings | None = None):
        """初始化 WebRTC 服务

        Args:
            consumer: ZeroMQ 消费者实例
        """
        self.consumer = consumer
        self.settings = settings or WebrtcSettings()
        self._connections: dict[str, RTCPeerConnection] = {}
        self._tracks: dict[str, CameraVideoTrack] = {}
        self._connection_meta: dict[str, WebRTCConnectionMeta] = {}
        self._total_connections_created = 0
        self._total_connections_closed = 0

    def get_available_cameras(self) -> list[str]:
        """获取当前可用的摄像头列表

        Returns:
            摄像头 ID 列表
        """
        frame = self.consumer.get_latest_frame()
        if frame is None:
            return []
        return [camera.component_id for camera in frame.cameras if camera.color_data is not None]

    async def create_offer(self, client_id: str, camera_id: str = "camera1") -> dict:
        """创建 WebRTC offer

        Args:
            client_id: 客户端标识
            camera_id: 摄像头 ID，默认 "camera1"

        Returns:
            包含 sdp 和 type 的字典
        """
        pc = RTCPeerConnection()
        self._connections[client_id] = pc
        self._connection_meta[client_id] = self._build_connection_meta(camera_id)
        self._total_connections_created += 1

        video_track = CameraVideoTrack(self.consumer, camera_id, self.settings.preview)
        sender = pc.addTrack(video_track)
        self._tracks[client_id] = video_track
        self._apply_codec_preferences(pc, sender)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    async def handle_answer(self, client_id: str, answer: dict) -> None:
        """处理客户端的 answer

        Args:
            client_id: 客户端标识
            answer: 包含 sdp 和 type 的字典
        """
        pc = self._connections.get(client_id)
        if pc:
            from aiortc import RTCSessionDescription

            await pc.setRemoteDescription(RTCSessionDescription(**answer))

    async def close_connection(self, client_id: str) -> None:
        """关闭指定客户端的连接

        Args:
            client_id: 客户端标识
        """
        pc = self._connections.pop(client_id, None)
        self._tracks.pop(client_id, None)
        self._connection_meta.pop(client_id, None)
        if pc:
            self._total_connections_closed += 1
            await pc.close()

    def _build_connection_meta(self, camera_id: str) -> WebRTCConnectionMeta:
        return WebRTCConnectionMeta(camera_id=camera_id, created_at=datetime.now(timezone.utc))

    def _apply_codec_preferences(self, pc: RTCPeerConnection, sender: RTCRtpSender) -> None:
        if not self.settings.codec.enabled or not self.settings.codec.preferred:
            return
        transceiver = next((item for item in pc.getTransceivers() if item.sender == sender), None)
        if transceiver is None:
            return
        capabilities = RTCRtpSender.getCapabilities("video").codecs
        preferred: list = []
        for mime_type in self.settings.codec.preferred:
            preferred.extend(codec for codec in capabilities if codec.mimeType == mime_type)
        preferred.extend(
            codec
            for codec in capabilities
            if codec.mimeType.lower() == "video/rtx" and codec not in preferred
        )
        if preferred:
            transceiver.setCodecPreferences(preferred)

    def get_debug_info(self) -> WebRTCDebugInfo:
        connections = []
        for client_id, pc in self._connections.items():
            meta = self._connection_meta.get(client_id)
            track = self._tracks.get(client_id)
            if meta is None or track is None:
                continue
            connections.append(
                WebRTCConnectionDebugInfo(
                    client_id=client_id,
                    camera_id=meta.camera_id,
                    created_at=meta.created_at,
                    connection_state=getattr(pc, "connectionState", None),
                    ice_connection_state=getattr(pc, "iceConnectionState", None),
                    signaling_state=getattr(pc, "signalingState", None),
                    track=track.get_debug_info(),
                )
            )
        return WebRTCDebugInfo(
            active_connections=len(self._connections),
            total_connections_created=self._total_connections_created,
            total_connections_closed=self._total_connections_closed,
            available_cameras=self.get_available_cameras(),
            preview_settings=WebRTCPreviewSettingsDebug(
                max_width=self.settings.preview.max_width,
                max_height=self.settings.preview.max_height,
                max_fps=self.settings.preview.max_fps,
                max_encoded_frame_bytes=self.settings.preview.max_encoded_frame_bytes,
            ),
            connections=connections,
        )
