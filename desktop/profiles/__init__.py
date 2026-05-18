"""Profile 包：导入所有机型模块触发 @register 注册。"""

from desktop.profiles import cr1, cr4a, cr4c, w1  # noqa: F401
from desktop.profiles.base import RobotProfile
from desktop.profiles.registry import load_profile, register, registered_profiles

__all__ = ["RobotProfile", "load_profile", "register", "registered_profiles"]
