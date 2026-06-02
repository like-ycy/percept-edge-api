"""CR 系列（CR1 / CR4A / CR4C）启动流水线。

标准链路：roscore → CAN → ros_slave → ros_master → robot_os → api → nginx。
VR 链路：默认 robot_os → api → nginx；若启用托管 VR ROS，则
roscore → CAN → VR arm → VR serial → VR 准备确认 → robot_os → api → nginx。
机型差异通过 profile.extra 开关：
    skip_can:         默认 False
    skip_ros_master:  默认 False（若机型只用 slave 可置 True）
    skip_nginx:       默认 False
    grace_overrides:  dict[str, float]，覆盖 GRACE_CHECK 的 duration
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from desktop.flows.base import OnFail, Step, StepKind
from desktop.models.runtime_state import RuntimeState
from desktop.profiles.base import RobotProfile
from desktop.services.config_loader import RuntimeConfig


def build_cr_flow(profile: RobotProfile, cfg: RuntimeConfig) -> Sequence[Step]:
    flags: Mapping[str, Any] = profile.extra
    vr_mode = cfg.launch_mode == "vr"
    managed_vr_ros = vr_mode and cfg.vr_ros_enabled and _supports_managed_vr_ros(profile)
    skip_can = (vr_mode and not managed_vr_ros) or bool(flags.get("skip_can", False))
    skip_ros = (vr_mode and not managed_vr_ros) or bool(flags.get("skip_ros", False))
    skip_ros_master = skip_ros or managed_vr_ros or bool(flags.get("skip_ros_master", False))
    skip_nginx = bool(flags.get("skip_nginx", False))
    grace = flags.get("grace_overrides") or {}

    roscore_grace = float(grace.get("roscore", cfg.roscore_startup_grace))
    slave_grace = float(grace.get("ros_slave", cfg.ros_startup_grace))
    master_grace = float(grace.get("ros_master", cfg.ros_startup_grace))
    vr_arm_grace = float(grace.get("vr_ros_arm", cfg.ros_startup_grace))
    vr_serial_grace = float(grace.get("vr_ros_serial", cfg.ros_startup_grace))

    steps: list[Step] = []

    if not skip_ros:
        steps.extend(
            [
                Step(StepKind.SPAWN, adapter="roscore", stage_label="系统准备"),
                Step(
                    StepKind.GRACE_CHECK,
                    adapter="roscore",
                    duration=roscore_grace,
                    stage_label="系统准备",
                ),
            ]
        )

    if not skip_can:
        steps.append(Step(StepKind.RUN_ONCE, adapter="can_init", stage_label="系统准备"))

    if managed_vr_ros:
        steps.extend(
            [
                Step(StepKind.SPAWN, adapter="vr_ros_arm", stage_label="VR ROS"),
                Step(
                    StepKind.GRACE_CHECK,
                    adapter="vr_ros_arm",
                    duration=vr_arm_grace,
                    stage_label="VR ROS",
                ),
                Step(StepKind.SPAWN, adapter="vr_ros_serial", stage_label="VR ROS"),
                Step(
                    StepKind.GRACE_CHECK,
                    adapter="vr_ros_serial",
                    duration=vr_serial_grace,
                    stage_label="VR ROS",
                ),
                Step(
                    StepKind.GATE,
                    stage_label="VR 准备确认",
                    extra={"name": "vr_ready_confirmation"},
                ),
            ]
        )
    elif not skip_ros:
        steps.extend(
            [
                Step(StepKind.SPAWN, adapter="ros_slave", stage_label="ROS 从臂"),
                Step(
                    StepKind.GRACE_CHECK,
                    adapter="ros_slave",
                    duration=slave_grace,
                    stage_label="ROS 从臂",
                ),
            ]
        )

    if not skip_ros_master:
        steps.extend(
            [
                Step(StepKind.SPAWN, adapter="ros_master", stage_label="ROS 主臂"),
                Step(
                    StepKind.GRACE_CHECK,
                    adapter="ros_master",
                    duration=master_grace,
                    stage_label="ROS 主臂",
                ),
            ]
        )

    steps.extend(
        [
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
        ]
    )

    if not skip_nginx:
        steps.append(
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
            )
        )

    return tuple(steps)


def _supports_managed_vr_ros(profile: RobotProfile) -> bool:
    adapter_names = {adapter.name for adapter in profile.adapters}
    return {"vr_ros_arm", "vr_ros_serial"}.issubset(adapter_names)
