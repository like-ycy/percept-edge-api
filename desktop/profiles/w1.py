"""W1 机型 profile：仅托管 robot_os 与 api。

W1 不由 desktop 启动 ROS 节点，但 robot_os.pyz 仍依赖 ROS Python 消息包，
因此启动 Robot OS 前需要 source ROS setup，让 diagnostic_msgs 等包可见。
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
            RobotOsAdapter(source_ros=True),
            ApiAdapter(),
        ),
        flow_factory=build_w1_flow,
        ros_required=False,
        can_required=False,
        launch_modes=("vr",),
    )
