"""CR1 专用启动流水线。"""

from __future__ import annotations

from typing import Sequence

from desktop.flows.base import OnFail, Step, StepKind
from desktop.models.runtime_state import RuntimeState
from desktop.profiles.base import RobotProfile
from desktop.services.config_loader import RuntimeConfig


def build_cr1_flow(profile: RobotProfile, cfg: RuntimeConfig) -> Sequence[Step]:
    """启动顺序对齐 scripts/robots/robot-cr1.sh。

    CR1 没有 VR 模式：固定为 roscore → CAN(0..4) → 四个 L5.launch → robot_os → API → nginx。
    """
    del profile

    return (
        Step(StepKind.SPAWN, adapter="roscore", stage_label="系统准备"),
        Step(
            StepKind.GRACE_CHECK,
            adapter="roscore",
            duration=cfg.roscore_startup_grace,
            stage_label="系统准备",
        ),
        Step(StepKind.RUN_ONCE, adapter="can_init", stage_label="系统准备"),
        Step(StepKind.SPAWN, adapter="ros_master1", stage_label="ROS master1"),
        Step(
            StepKind.GRACE_CHECK,
            adapter="ros_master1",
            duration=cfg.ros_startup_grace,
            stage_label="ROS master1",
        ),
        Step(StepKind.SPAWN, adapter="ros_pos_follow1", stage_label="ROS pos_follow1"),
        Step(
            StepKind.GRACE_CHECK,
            adapter="ros_pos_follow1",
            duration=cfg.ros_startup_grace,
            stage_label="ROS pos_follow1",
        ),
        Step(StepKind.SPAWN, adapter="ros_master2", stage_label="ROS master2"),
        Step(
            StepKind.GRACE_CHECK,
            adapter="ros_master2",
            duration=cfg.ros_startup_grace,
            stage_label="ROS master2",
        ),
        Step(StepKind.SPAWN, adapter="ros_pos_follow2", stage_label="ROS pos_follow2"),
        Step(
            StepKind.GRACE_CHECK,
            adapter="ros_pos_follow2",
            duration=cfg.ros_startup_grace,
            stage_label="ROS pos_follow2",
        ),
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
