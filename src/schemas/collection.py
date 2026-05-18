# src/schemas/collection.py
"""采集相关 Schema"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class CollectionStatusEnum(str, Enum):
    """采集状态枚举"""

    IDLE = "idle"
    COLLECTING = "collecting"
    STOPPING = "stopping"
    ABORTED = "aborted"
    ERROR = "error"


class CollectionRecordStatusEnum(str, Enum):
    """采集记录状态枚举"""

    COLLECTING = "collecting"
    FINALIZING = "finalizing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FINALIZE_FAILED = "finalize_failed"
    VALIDATION_FAILED = "validation_failed"


class CollectionSession(BaseModel):
    """采集会话信息"""

    task_id: int
    status: CollectionStatusEnum
    start_time: datetime | None = None
    frame_count: int = 0
    output_dir: str | None = None
    error_message: str | None = None  # 错误信息（仅在 status=ERROR 时有值）


class CollectionLockState(BaseModel):
    """采集全局锁状态"""

    locked: bool
    reason: str | None = None
    triggered_record_id: int | None = None
    triggered_at: datetime | None = None
    released_at: datetime | None = None
    released_by: str | None = None
    release_note: str | None = None


class CollectionLockReleaseRequest(BaseModel):
    """采集锁解锁请求体"""

    operator: str
    note: str | None = None


class RawCaptureInfo(BaseModel):
    """原始 bin 数据概要"""

    record_id: int
    output_dir: str | None
    capture_dir: str
    sealed: bool
    frame_count: int
    raw_bytes: int
    start_time: str | None
    end_time: str | None
    segment_count: int
    segments: list[str]
    cameras: list[str]
    camera_count: int
    sampled_frames: int


# ========== ZeroMQ 解析模型（使用 dataclass 优化性能）==========
# 注意：这些模型用于高频数据流（30fps+），使用 dataclass 替代 Pydantic
# 以避免每帧数据的类型验证开销。


@dataclass(slots=True)
class CameraFrame:
    """摄像头帧数据

    使用 slots=True 优化内存占用和访问速度。
    """

    component_id: str
    timestamp: float
    color_data: bytes | None = None
    depth_data: bytes | None = None


@dataclass(slots=True)
class ArmFrame:
    """机械臂帧数据（关节 + 末端执行器合一）

    新格式中每个机械臂组件（如 slave_arm1）同时包含 joint_data 和 eef_data。
    使用 slots=True 优化内存占用和访问速度。
    """

    component_id: str
    timestamp: float
    # joint_data
    joint_pos: list[float] = field(default_factory=list)
    joint_vel: list[float] = field(default_factory=list)
    joint_cur: list[float] = field(default_factory=list)
    joint_eff: list[float] = field(default_factory=list)
    joint_force: list[float] = field(default_factory=list)
    # eef_data（gripper_data 存在时取 eef_data[:-1]；null 保持为 None）
    eef: list[float] | None = None
    gripper: list[float] | None = None


@dataclass(slots=True)
class ExtraComponentFrame:
    """非相机/机械臂组件的原始帧数据

    用于承接 agv、lift、vr 等新版映射中需要落到 JSON 的扩展组件。
    """

    component_id: str
    timestamp: float
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ZmqFrame:
    """ZeroMQ 一帧完整数据

    使用 dataclass 替代 Pydantic 以优化高频数据流性能。
    对于 30fps 视频流，每帧避免 Pydantic 验证可节省显著 CPU 开销。
    """

    timestamp: float
    cameras: list[CameraFrame] = field(default_factory=list)
    arms: list[ArmFrame] = field(default_factory=list)
    extras: list[ExtraComponentFrame] = field(default_factory=list)
