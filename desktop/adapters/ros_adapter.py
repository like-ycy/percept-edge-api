"""ROS 封装：roscore / roslaunch slave / roslaunch master。

约定：
    ctx.extra["config"] -> RuntimeConfig（见 desktop.services.config_loader）
Adapter 从 RuntimeConfig 的字段渲染 ProcessSpec，不关心 flow 编排。
"""

from __future__ import annotations

import signal as _signal
from typing import Literal, Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep

RosRole = Literal["core", "slave", "master"]

_DEFAULT_NAMES: dict[str, str] = {
    "core": "roscore",
    "slave": "ros_slave",
    "master": "ros_master",
}

_LOG_LABELS: dict[str, str] = {
    "core": "SYS_PREP",
    "slave": "ROS_SLAVE",
    "master": "ROS_MASTER",
}


class RosAdapter(Adapter):
    """参数化的 ROS 进程封装。同一机型可多次实例化（core/slave/master 各一）。"""

    def __init__(self, role: RosRole, name: Optional[str] = None) -> None:
        if role not in _DEFAULT_NAMES:
            raise ValueError(f"未知 ROS role: {role}")
        self.role: RosRole = role
        self.name: str = name or _DEFAULT_NAMES[role]
        self.log_label: str = _LOG_LABELS[role]

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        cmd_field = {
            "core": cfg.roscore_cmd,
            "slave": cfg.ros_slave_cmd,
            "master": cfg.ros_master_cmd,
        }[self.role]
        return ProcessSpec(
            name=self.name,
            cmd=f"source '{cfg.ros_setup_script}' && exec {cmd_field}",
            cwd=cfg.ros_env_cwd,
            shell=True,
            shutdown_signal=int(_signal.SIGINT),
            shutdown_grace=cfg.ros_shutdown_grace,
            force_kill_grace=cfg.force_kill_grace,
            shutdown_sequence=(
                ShutdownStep(signal=int(_signal.SIGINT), grace=cfg.ros_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGTERM), grace=cfg.sigterm_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGKILL), grace=cfg.force_kill_grace),
            ),
        )

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]:
        # ROS 启动无显式就绪端点，由 flow 使用 grace 窗口判断
        del ctx
        return None


class VrRosAdapter(Adapter):
    """CR4A/CR4C VR 模式 ROS 进程封装。"""

    def __init__(self, role: Literal["arm", "serial"]) -> None:
        if role not in {"arm", "serial"}:
            raise ValueError(f"未知 VR ROS role: {role}")
        self.role = role
        self.name = f"vr_ros_{role}"
        self.log_label = "VR_ROS"

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        if self.role == "arm":
            cwd = cfg.vr_ros_arm_cwd
            setup_script = cfg.vr_ros_arm_setup_script
            command = cfg.vr_ros_arm_cmd
        else:
            cwd = cfg.vr_ros_serial_cwd
            setup_script = cfg.vr_ros_serial_setup_script
            command = cfg.vr_ros_serial_cmd
        return ProcessSpec(
            name=self.name,
            cmd=f"source '{setup_script}' && exec {command}",
            cwd=cwd,
            shell=True,
            shutdown_signal=int(_signal.SIGINT),
            shutdown_grace=cfg.ros_shutdown_grace,
            force_kill_grace=cfg.force_kill_grace,
            shutdown_sequence=(
                ShutdownStep(signal=int(_signal.SIGINT), grace=cfg.ros_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGTERM), grace=cfg.sigterm_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGKILL), grace=cfg.force_kill_grace),
            ),
        )

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]:
        del ctx
        return None
