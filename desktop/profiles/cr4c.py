"""CR4C 机型 profile：roscore + slave + master + robot_os(cr4c.pyz) + api + nginx。"""

from __future__ import annotations

from desktop.adapters.api_adapter import ApiAdapter
from desktop.adapters.can_adapter import CanAdapter
from desktop.adapters.robot_os_adapter import RobotOsAdapter
from desktop.adapters.ros_adapter import RosAdapter, VrRosAdapter
from desktop.flows.cr_flow import build_cr_flow
from desktop.profiles.base import RobotProfile
from desktop.profiles.registry import register


@register("robot-cr4c")
def _profile() -> RobotProfile:
    return RobotProfile(
        robot_name="robot-cr4c",
        display_name="CR4C",
        adapters=(
            RosAdapter(role="core"),
            RosAdapter(role="slave"),
            RosAdapter(role="master"),
            VrRosAdapter(role="arm"),
            VrRosAdapter(role="serial"),
            CanAdapter(),
            RobotOsAdapter(),
            ApiAdapter(),
        ),
        flow_factory=build_cr_flow,
        ros_required=True,
        can_required=True,
        launch_modes=("bilateral", "vr"),
    )
