"""CAN 初始化封装：一次性 bash 脚本，将 /dev/arxcan 设备通过 slcand 拉起。

属于 one-shot 动作而非长驻进程。Flow 应等待 finished(exit_code=0) 后继续；
非零退出视为失败（exit_code=16 表示 CAN 设备缺失或初始化失败）。
"""

from __future__ import annotations

from typing import Optional, Sequence

from desktop.adapters._common import get_config
from desktop.adapters.base import Adapter, BuildContext, HealthProbe, ProcessSpec


class CanAdapter(Adapter):
    """slcand 初始化。one-shot 语义。"""

    def __init__(
        self,
        name: str = "can_init",
        *,
        device_indices: Sequence[int] = (0, 1, 2, 3),
    ) -> None:
        self.name: str = name
        self.log_label: str = "SYS_PREP"
        self._device_indices = tuple(device_indices)

    def build_spec(self, ctx: BuildContext) -> ProcessSpec:
        cfg = get_config(ctx)
        return ProcessSpec(
            name=self.name,
            cmd=self._build_init_script(),
            cwd=str(cfg.repo_root),
            shell=True,
            shutdown_grace=cfg.process_shutdown_grace,
            force_kill_grace=cfg.force_kill_grace,
        )

    def build_probe(self, ctx: BuildContext) -> Optional[HealthProbe]:
        del ctx
        return None

    def _build_init_script(self) -> str:
        indices = " ".join(str(index) for index in self._device_indices)
        return (
            f"for index in {indices}; do "
            'if [ ! -e "/dev/arxcan${index}" ]; then exit 16; fi; '
            'sudo -n slcand -o -f -s8 "/dev/arxcan${index}" "can${index}" '
            ">/dev/null 2>&1 || exit 16; "
            'sudo -n ifconfig "can${index}" up >/dev/null 2>&1 || exit 16; '
            "sleep 1; "
            "done"
        )
