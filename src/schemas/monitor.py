# src/schemas/monitor.py
"""Monitor 相关 Schema"""

from pydantic import BaseModel


class CpuInfo(BaseModel):
    """CPU 信息"""

    total_cores: int
    physical_cores: int
    cpu_percent: float
    per_cpu_percent: list[float]
    freq_min: float
    freq_max: float


class MemoryInfo(BaseModel):
    """内存信息"""

    total_gb: float
    available_gb: float
    used_gb: float
    free_gb: float
    percent: float


class DiskInfo(BaseModel):
    """磁盘信息"""

    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


class PlatformInfo(BaseModel):
    """平台信息"""

    system: str
    platform: str
    release: str
    mac_address: str | None = None
    ip_address: str | None = None


class SystemInfo(BaseModel):
    """系统资源信息汇总"""

    cpu: CpuInfo
    memory: MemoryInfo
    disk: DiskInfo
    platform: PlatformInfo


class ComponentStatus(BaseModel):
    """ontology-core runtime_monitor v2 组件状态。"""

    component_id: str
    enabled: bool
    state: str
    connect_state: str
    role: str | None = None
    hz: int = 0
    last_frame_at_ms: int | None = None
    frame_count: int | None = None
    error_count: int | None = None
    last_error: str | None = None
    width: int | None = None
    height: int | None = None
    jpeg_quality: int | None = None
    depth_scale: float | None = None
    brand: str | None = None
    model: str | None = None
    detail: str | None = None
    joint_data_dim: int | None = None
    eef_data_dim: int | None = None
    gripper_data_dim: int | None = None
    joint_speed_dim: int | None = None
    current_dim: int | None = None
    effort_dim: int | None = None


class RobotMetadataInfo(BaseModel):
    """ontology-core runtime_monitor v2 机器人元数据。"""

    robot_id: str
    profile: str
    runtime_kind: str
    robot_model: str
    robot_type: str
    robot_desc: list[str]
    robot_register_info: dict[str, str] | None = None


class RobotStatus(BaseModel):
    """机器人整体状态"""

    metadata: RobotMetadataInfo
    components: list[ComponentStatus]
