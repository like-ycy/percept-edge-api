"""CR5 机型 profile：同 W1，仅启动 robot_os 与 api。"""

from __future__ import annotations

from desktop.adapters.api_adapter import ApiAdapter
from desktop.adapters.robot_os_adapter import RobotOsAdapter
from desktop.flows.w1_flow import build_w1_flow
from desktop.profiles.base import RobotProfile
from desktop.profiles.registry import register


@register("robot-cr5")
def _profile() -> RobotProfile:
    return RobotProfile(
        robot_name="robot-cr5",
        display_name="CR5",
        adapters=(
            RobotOsAdapter(source_ros=False),
            ApiAdapter(),
        ),
        flow_factory=build_w1_flow,
        ros_required=False,
        can_required=False,
        launch_modes=("bilateral",),
    )
