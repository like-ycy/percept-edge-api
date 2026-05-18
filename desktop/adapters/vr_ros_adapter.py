"""VR ROS 封装：VR 双臂 launch 与 serial_port 长驻进程。"""

from __future__ import annotations

import signal as _signal
from typing import Literal, Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep

VrRosRole = Literal["arm", "serial"]


class VrRosAdapter(Adapter):
    """VR 采集专用 ROS 进程封装。"""

    def __init__(self, role: VrRosRole) -> None:
        if role not in {"arm", "serial"}:
            raise ValueError(f"未知 VR ROS role: {role}")
        self.role: VrRosRole = role
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
            shutdown_sequence=(
                ShutdownStep(signal=int(_signal.SIGINT), grace=cfg.ros_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGTERM), grace=cfg.process_shutdown_grace),
            ),
            force_kill_grace=cfg.force_kill_grace,
        )

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]:
        del ctx
        return None
