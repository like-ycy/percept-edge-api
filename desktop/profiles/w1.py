"""W1 机型 profile：占位实现，仅启动 robot_os 与 api。

W1 实际外设依赖确认后再补充 adapter（CAN / 视觉等）与 flow。
"""

from __future__ import annotations

from desktop.adapters.api_adapter import ApiAdapter
from desktop.adapters.robot_os_adapter import RobotOsAdapter
from desktop.flows.w1_flow import build_w1_flow
from desktop.profiles.base import RobotProfile
from desktop.profiles.registry import register


@register("robot-w1")
def _profile() -> RobotProfile:
    return RobotProfile(
        robot_name="robot-w1",
        display_name="W1",
        adapters=(
            RobotOsAdapter(source_ros=False),
            ApiAdapter(),
        ),
        flow_factory=build_w1_flow,
        ros_required=False,
        can_required=False,
        launch_modes=("vr",),
    )
