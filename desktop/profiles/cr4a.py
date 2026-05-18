"""CR4A 机型 profile：与 CR4C 一致的 ROS + CAN + Robot OS + API + nginx 链路。

若 CR4A 实际启动命令与 CR4C 不同，请通过环境变量 ROBOT_OS_CMD 覆盖。
"""

from __future__ import annotations

from desktop.adapters.api_adapter import ApiAdapter
from desktop.adapters.can_adapter import CanAdapter
from desktop.adapters.robot_os_adapter import RobotOsAdapter
from desktop.adapters.ros_adapter import RosAdapter
from desktop.adapters.vr_ros_adapter import VrRosAdapter
from desktop.flows.cr_flow import build_cr_flow
from desktop.profiles.base import RobotProfile
from desktop.profiles.registry import register


@register("robot-cr4a")
def _profile() -> RobotProfile:
    return RobotProfile(
        robot_name="robot-cr4a",
        display_name="CR4A",
        adapters=(
            RosAdapter(role="core"),
            RosAdapter(role="slave"),
            RosAdapter(role="master"),
            CanAdapter(),
            VrRosAdapter(role="arm"),
            VrRosAdapter(role="serial"),
            RobotOsAdapter(),
            ApiAdapter(),
        ),
        flow_factory=build_cr_flow,
        ros_required=True,
        can_required=True,
        launch_modes=("bilateral", "vr"),
    )
