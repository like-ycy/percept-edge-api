"""进程监控相关 schema"""

from datetime import datetime

from pydantic import BaseModel


class ProcessInfo(BaseModel):
    """单个进程指标"""

    pid: int
    parent_pid: int | None
    name: str
    cmdline: str
    status: str
    create_time: datetime
    cpu_percent: float
    cpu_num: int | None
    cpu_affinity: list[int] | None
    num_threads: int
    memory_rss_mb: float
    memory_percent: float
    num_fds: int | None


class SystemOverview(BaseModel):
    """系统总览"""

    cpu_count_logical: int
    cpu_count_physical: int
    per_cpu_percent: list[float]
    load_avg_1_5_15: tuple[float, float, float]
    memory_total_mb: float
    memory_used_mb: float
    memory_percent: float
    platform: str


class ProcessMonitorResponse(BaseModel):
    """端点响应体"""

    sampled_at: datetime
    from_cache: bool
    main_pid: int
    system: SystemOverview
    processes: list[ProcessInfo]
