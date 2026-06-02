"""Percept Edge API 封装：启动 main.py，通过 HTTP 根路径探测就绪。"""

from __future__ import annotations

import signal as _signal
from typing import Optional

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec, ShutdownStep


class ApiAdapter(Adapter):
    """采集程序 API 进程 + HTTP 就绪探测。"""

    def __init__(self, name: str = "api") -> None:
        self.name: str = name
        self.log_label: str = "PERCEPT"

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        return ProcessSpec(
            name=self.name,
            cmd=f"export SERVER__DEBUG=false && exec {cfg.api_cmd}",
            cwd=cfg.api_cwd,
            shell=True,
            shutdown_signal=int(_signal.SIGTERM),
            shutdown_grace=cfg.process_shutdown_grace,
            force_kill_grace=cfg.force_kill_grace,
            shutdown_sequence=(
                ShutdownStep(signal=int(_signal.SIGTERM), grace=cfg.api_shutdown_grace),
                ShutdownStep(signal=int(_signal.SIGKILL), grace=cfg.force_kill_grace),
            ),
        )

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]:
        cfg = get_config(ctx)
        return HealthProbe(
            kind="http",
            target=cfg.api_url,
            timeout=1.0,
            interval=cfg.api_gate_interval,
            deadline=cfg.api_startup_timeout,
            expect={
                "status": 200,
                "match": {
                    "code": 200,
                    "data.message": "Percept Edge API",
                },
            },
        )
