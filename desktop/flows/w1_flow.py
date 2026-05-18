"""W1 启动流水线占位。

当前 W1 无 ROS / CAN 链路，VR 启动顺序：robot_os → api → nginx。
"""

from __future__ import annotations

from typing import Sequence

from desktop.flows.base import OnFail, Step, StepKind
from desktop.models.runtime_state import RuntimeState
from desktop.profiles.base import RobotProfile
from desktop.services.config_loader import RuntimeConfig


def build_w1_flow(profile: RobotProfile, cfg: RuntimeConfig) -> Sequence[Step]:
    del profile
    return (
        Step(StepKind.SPAWN, adapter="robot_os", stage_label="Robot OS"),
        Step(
            StepKind.WAIT_HEALTH,
            adapter="robot_os",
            stage_label="Robot OS",
            extra={"waiting_state": RuntimeState.WAITING_ROBOT_READY.value},
        ),
        Step(StepKind.SPAWN, adapter="api", stage_label="采集程序"),
        Step(
            StepKind.WAIT_HEALTH,
            adapter="api",
            stage_label="采集程序",
            extra={"waiting_state": RuntimeState.WAITING_API_READY.value},
        ),
        Step(
            StepKind.SHELL_ONCE,
            stage_label="采集程序",
            on_fail=OnFail.CONTINUE,
            extra={
                "name": "start_nginx",
                "cmd": "sudo -n systemctl start nginx",
                "cwd": str(cfg.repo_root),
                "log_label": "PERCEPT",
            },
        ),
    )
