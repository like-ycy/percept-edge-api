from dataclasses import dataclass

from desktop.models.runtime_health import RuntimeHealth
from desktop.models.stage_state import StageState


@dataclass
class RuntimeSnapshot:
    environment: str
    global_status: str
    runtime_state: str
    message: str
    running: bool
    stages: list[StageState]
    health: RuntimeHealth
    state_file: str
