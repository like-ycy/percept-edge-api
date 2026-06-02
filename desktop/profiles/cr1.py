"""CR1 机型 profile。"""

from __future__ import annotations

from desktop.adapters.api_adapter import ApiAdapter
from desktop.adapters.can_adapter import CanAdapter
from desktop.adapters.cr1_ros_adapter import Cr1RosAdapter
from desktop.adapters.robot_os_adapter import RobotOsAdapter
from desktop.flows.cr1_flow import build_cr1_flow
from desktop.profiles.base import RobotProfile
from desktop.profiles.registry import register


@register("robot-cr1")
def _profile() -> RobotProfile:
    return RobotProfile(
        robot_name="robot-cr1",
        display_name="CR1",
        adapters=(
            Cr1RosAdapter(name="roscore", log_label="SYS_PREP"),
            Cr1RosAdapter(name="ros_master1", log_label="ROS_MASTER1"),
            Cr1RosAdapter(name="ros_pos_follow1", log_label="ROS_POS_FOLLOW1"),
            Cr1RosAdapter(name="ros_master2", log_label="ROS_MASTER2"),
            Cr1RosAdapter(name="ros_pos_follow2", log_label="ROS_POS_FOLLOW2"),
            CanAdapter(can_indices=(0, 1, 2, 3, 4)),
            RobotOsAdapter(),
            ApiAdapter(),
        ),
        flow_factory=build_cr1_flow,
        ros_required=True,
        can_required=True,
        launch_modes=("bilateral",),
    )
