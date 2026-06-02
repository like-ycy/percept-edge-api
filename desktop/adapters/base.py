"""Adapter 层契约。

Adapter 只描述单个外部进程的启动规格与健康检查规格，不负责生命周期控制。
生命周期由 services.process_manager / services.health_checker 统一执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ShutdownStep:
    """单个停止阶段：发送 signal 后等待 grace 秒。"""

    signal: int
    grace: float


@dataclass(frozen=True)
class ProcessSpec:
    """进程启动规格，由 process_manager 消费。"""

    name: str
    cmd: str
    cwd: str
    env: Mapping[str, str] = field(default_factory=dict)
    shell: bool = True
    shutdown_signal: int = 15
    shutdown_grace: float = 5.0
    force_kill_grace: float = 2.0
    shutdown_sequence: tuple[ShutdownStep, ...] = ()

    def effective_shutdown_sequence(self) -> tuple[ShutdownStep, ...]:
        """返回进程停止序列；未配置多阶段时保持旧单信号兼容。"""
        if self.shutdown_sequence:
            return self.shutdown_sequence
        return (ShutdownStep(signal=int(self.shutdown_signal), grace=self.shutdown_grace),)


@dataclass(frozen=True)
class HealthProbe:
    """健康检查规格，由 health_checker 消费。

    kind:
        - "http"          target=URL，expect={"status": 200, "json_path": ["data","ready"]}
        - "zmq_monitor"   target=command_endpoint，expect={"timeout": 0.5}
        - "pid"           target="" 仅检查进程存活
        - "custom"        由调用方扩展
    """

    kind: str
    target: str = ""
    timeout: float = 1.0
    interval: float = 1.0
    deadline: float = 30.0
    expect: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class BuildContext:
    """渲染 ProcessSpec / HealthProbe 时的上下文。

    由 profile + config_loader 组装，Adapter.build_* 方法消费。
    进程环境变量由 SequentialFlowRunner 通过 env_extra 注入，不经 BuildContext。
    """

    repo_root: Path
    robot_name: str
    uv_bin: str
    extra: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Adapter(Protocol):
    """Adapter 纯函数式：只产出规格，不执行。

    log_label: 日志面板中该进程输出的来源标签（如 "PERCEPT"/"ROBOT_OS"）。
               多个 adapter 可共用同一 label（如 roscore + can_init 都属 SYS_PREP）。
    """

    name: str
    log_label: str

    def build_spec(self, ctx: BuildContext) -> ProcessSpec: ...

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]: ...
