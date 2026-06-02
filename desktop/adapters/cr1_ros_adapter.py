"""CR1 专用 ROS 进程封装。"""

from __future__ import annotations

import signal as _signal
from typing import Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep


def _quiet_roslaunch(cmd: str) -> str:
    if cmd.startswith("roslaunch "):
        return f"{cmd} >/dev/null 2>&1"
    return cmd


class Cr1RosAdapter(Adapter):
    """CR1 多 ROS 工作空间进程封装。"""

    def __init__(self, name: str, log_label: str) -> None:
        self.name: str = name
        self.log_label: str = log_label

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        command = next((item for item in cfg.ros_commands if item.name == self.name), None)
        if command is None:
            raise KeyError(f"CR1 缺少 ROS 命令配置: {self.name}")
        prefix = f"export PATH='{command.path_prefix}':$PATH; " if command.path_prefix else ""
        command_text = _quiet_roslaunch(command.cmd)
        return ProcessSpec(
            name=self.name,
            cmd=f"{prefix}source '{command.setup_script}' && exec {command_text}",
            cwd=command.cwd,
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
