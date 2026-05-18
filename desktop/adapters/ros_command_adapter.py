"""固定命令 ROS 进程封装，用于 CR1 这类非 core/slave/master 结构。"""

from __future__ import annotations

import signal as _signal
from typing import Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep


class RosCommandAdapter(Adapter):
    """通过显式 cwd/setup/cmd 启动一个 ROS 进程。"""

    def __init__(
        self,
        *,
        name: str,
        log_label: str,
        cwd: str,
        setup_script: str,
        cmd: str,
        path_prefix: str | None = None,
    ) -> None:
        self.name = name
        self.log_label = log_label
        self._cwd = cwd
        self._setup_script = setup_script
        self._cmd = cmd
        self._path_prefix = path_prefix

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        path_prefix = ""
        if self._path_prefix:
            path_prefix = f"export PATH='{self._path_prefix}':$PATH; "
        return ProcessSpec(
            name=self.name,
            cmd=f"{path_prefix}source '{self._setup_script}' && exec {self._cmd}",
            cwd=self._cwd,
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
