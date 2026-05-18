from dataclasses import dataclass
from typing import Optional


@dataclass
class RuntimeHealth:
    overall: str = "unknown"
    cloud_api: str = "unknown"
    robot_os: str = "unknown"
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    robot_components: int = 0
