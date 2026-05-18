"""Profile 注册表。

机型实现文件（cr1.py / cr4a.py / cr4c.py / w1.py）通过 @register 注册自身工厂，
load_profile 根据 PERCEPT_ROBOT 环境变量或显式参数返回对应 RobotProfile。
"""

from __future__ import annotations

import os
from typing import Callable, Dict

from desktop.profiles.base import RobotProfile

ProfileFactory = Callable[[], RobotProfile]

_REGISTRY: Dict[str, ProfileFactory] = {}


def register(name: str) -> Callable[[ProfileFactory], ProfileFactory]:
    def deco(fn: ProfileFactory) -> ProfileFactory:
        if name in _REGISTRY:
            raise ValueError(f"profile 重复注册: {name}")
        _REGISTRY[name] = fn
        return fn

    return deco


def load_profile(name: str | None = None) -> RobotProfile:
    key = name or os.getenv("PERCEPT_ROBOT") or os.getenv("APP_ROBOT") or "robot-cr4c"
    try:
        factory = _REGISTRY[key]
    except KeyError as exc:
        raise KeyError(f"未注册的 profile: {key}; 已注册: {sorted(_REGISTRY)}") from exc
    return factory()


def registered_profiles() -> list[str]:
    return sorted(_REGISTRY)
