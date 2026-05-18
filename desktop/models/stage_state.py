from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StageStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class StageState:
    name: str
    status: StageStatus = StageStatus.IDLE
    dependency: Optional[str] = None
    pid: Optional[str] = None
    summary: str = "空闲"
    last_error: Optional[str] = None
    details: list[str] = field(default_factory=list)
