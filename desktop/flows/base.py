"""Flow 层契约。

FlowRunner 消费一组 Step，按顺序驱动 process_manager / health_checker，
并通过 FlowEvent 回调通知上层 UI。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from desktop.models.stage_state import StageStatus

__all__ = [
    "FlowEvent",
    "FlowEventCallback",
    "FlowRunner",
    "OnFail",
    "StageStatus",
    "Step",
    "StepKind",
]


class StepKind(str, Enum):
    SPAWN = "spawn"  # 启动长驻进程，等 started 信号后推进
    GRACE_CHECK = "grace_check"  # 休眠 duration 秒，然后校验指定进程仍存活
    RUN_ONCE = "run_once"  # 启动一次性进程，等待退出码 0
    WAIT_HEALTH = "wait_health"  # 轮询 adapter.build_probe() 直到就绪或超时
    SLEEP = "sleep"  # 纯休眠，不做任何校验
    SHELL_ONCE = "shell_once"  # 执行 extra 中的 shell 命令（name/cmd/cwd），等退出码 0
    GATE = "gate"  # 自定义回调，extra["fn"] 接收 runner 自行推进


class OnFail(str, Enum):
    ABORT = "abort"
    CONTINUE = "continue"


@dataclass(frozen=True)
class Step:
    kind: StepKind
    adapter: Optional[str] = None
    duration: float = 0.0
    stage_label: str = ""
    on_fail: OnFail = OnFail.ABORT
    parallel_group: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FlowEvent:
    step_index: int
    stage: str
    status: StageStatus
    message: str = ""
    payload: Optional[Mapping[str, Any]] = None


FlowEventCallback = Callable[[FlowEvent], None]


@runtime_checkable
class FlowRunner(Protocol):
    steps: Sequence[Step]

    def start(self, on_event: FlowEventCallback) -> None: ...

    def stop(self, *, stop_processes: bool = True) -> None: ...

    def is_stopped(self) -> bool: ...
