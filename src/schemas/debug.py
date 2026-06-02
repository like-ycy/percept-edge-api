"""调试相关 Schema"""

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

    # watchdog 状态
    watchdog_enabled: bool = False
    is_stale: bool = False
    stale_seconds: float | None = None  # 距离最后一次收到数据经过的秒数
    stale_threshold_seconds: float | None = None
