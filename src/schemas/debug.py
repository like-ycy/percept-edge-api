"""调试相关 Schema"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FrameMetadata(BaseModel):
    """帧元数据（不含实际数据）"""

    timestamp: datetime | None = None

    # 图像元数据
    has_image: bool = False
    image_shape: tuple[int, int, int] | None = None  # (height, width, channels)
    image_dtype: str | None = None

    # 机器人状态元数据
    has_robot_state: bool = False
    robot_state_fields: list[str] = []


class ZeroMQDebugInfo(BaseModel):
    """ZeroMQ 调试信息"""

    # 连接状态
    is_connected: bool
    endpoint: str

    # 统计信息
    frames_received: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    frames_flushed: int = 0  # 采集开始时清空的旧帧数
    last_receive_time: datetime | None = None

    # 队列状态
    queue_size: int = 0
    queue_capacity: int = 100
    is_collection_enabled: bool = False  # 是否处于采集模式

    # 性能指标
    frame_delay_ms: float | None = None  # 端到端延迟（当前时间 - 帧时间戳）
    parse_time_ms: float | None = None  # 解析耗时
    last_frame_timestamp: float | None = None  # 最新帧的原始时间戳

    # 最新帧元数据
    latest_frame: FrameMetadata | None = None

    # 运行时间
    uptime_seconds: float = 0

    # 帧率 (fps)
    fps: float = 0.0  # 平均帧率 (frames_received / uptime_seconds)


class WebRTCPreviewSettingsDebug(BaseModel):
    """WebRTC 预览配置调试信息。"""

    max_width: int
    max_height: int
    max_fps: float
    max_encoded_frame_bytes: int


class WebRTCTrackDebugInfo(BaseModel):
    """单路 WebRTC 预览轨道调试信息。"""

    frames_requested: int = 0
    frames_sent: int = 0
    frames_reused: int = 0
    fallback_frames: int = 0
    oversized_frames_skipped: int = 0
    decode_failures: int = 0
    resize_count: int = 0
    last_frame_at: datetime | None = None
    last_encoded_bytes: int | None = None
    last_source_width: int | None = None
    last_source_height: int | None = None
    last_output_width: int | None = None
    last_output_height: int | None = None
    last_decode_ms: float | None = None
    last_resize_ms: float | None = None
    last_total_recv_ms: float | None = None
    active_profile: str | None = None


class WebRTCConnectionDebugInfo(BaseModel):
    """WebRTC 连接调试信息。"""

    client_id: str
    camera_id: str
    created_at: datetime
    connection_state: str | None = None
    ice_connection_state: str | None = None
    signaling_state: str | None = None
    track: WebRTCTrackDebugInfo


class WebRTCDebugInfo(BaseModel):
    """WebRTC 服务调试信息。"""

    active_connections: int
    total_connections_created: int
    total_connections_closed: int
    available_cameras: list[str]
    preview_settings: WebRTCPreviewSettingsDebug
    connections: list[WebRTCConnectionDebugInfo]
