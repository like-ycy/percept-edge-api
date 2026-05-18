from enum import Enum


class RuntimeState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    WAITING_ROBOT_READY = "waiting_robot_ready"
    WAITING_API_READY = "waiting_api_ready"
    RUNNING = "running"
    STOPPING = "stopping"
    TERMINATED = "terminated"
    ERROR = "error"
