"""Adapter 内部共享工具（非公开接口）。"""

from __future__ import annotations

from desktop.adapters.base import BuildContext
from desktop.services.config_loader import RuntimeConfig


def get_config(ctx: BuildContext) -> RuntimeConfig:
    cfg = ctx.extra.get("config")
    if not isinstance(cfg, RuntimeConfig):
        raise KeyError("BuildContext.extra['config'] 必须为 RuntimeConfig 实例")
    return cfg
