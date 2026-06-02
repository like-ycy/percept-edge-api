"""显式 ROS 命令封装，用于多工作空间或测试场景。"""

from __future__ import annotations

import signal as _signal
from typing import Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep


class RosCommandAdapter(Adapter):
    """从构造参数直接渲染 ROS 命令的 adapter。"""

    def __init__(
        self,
        *,
        name: str,
        log_label: str,
        cwd: str,
        setup_script: str,
        cmd: str,
        path_prefix: str = "",
    ) -> None:
        self.name = name
        self.log_label = log_label
        self._cwd = cwd
        self._setup_script = setup_script
        self._cmd = cmd
        self._path_prefix = path_prefix

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        prefix = f"export PATH='{self._path_prefix}':$PATH; " if self._path_prefix else ""
        return ProcessSpec(
            name=self.name,
            cmd=f"{prefix}source '{self._setup_script}' && exec {self._cmd}",
            cwd=self._cwd,
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
