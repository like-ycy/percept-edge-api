"""CR1 机型 profile：对齐 scripts/robots/robot-cr1.sh 的实机启动链路。"""

from __future__ import annotations

from desktop.adapters.api_adapter import ApiAdapter
from desktop.adapters.can_adapter import CanAdapter
from desktop.adapters.robot_os_adapter import RobotOsAdapter
from desktop.adapters.ros_command_adapter import RosCommandAdapter
from desktop.flows.cr1_flow import build_cr1_flow
from desktop.profiles.base import RobotProfile
from desktop.profiles.registry import register


_CONDA_BIN = "/home/agilex/miniconda3/envs/py310/bin"
_ROS_WORKSPACE_DIR = "/home/agilex/workspaces/ros_env"
_ROBOTIC_ARM_WS = f"{_ROS_WORKSPACE_DIR}/robotic_arm_ws"
_ROS_NODE_CWDS = (
    ("roscore", f"{_ROBOTIC_ARM_WS}/follow1"),
    ("ros_master1", f"{_ROBOTIC_ARM_WS}/master1"),
    ("ros_pos_follow1", f"{_ROBOTIC_ARM_WS}/pos_follow1"),
    ("ros_master2", f"{_ROBOTIC_ARM_WS}/master2"),
    ("ros_pos_follow2", f"{_ROBOTIC_ARM_WS}/pos_follow2"),
)


def _ros_adapter(name: str, log_label: str, cwd: str) -> RosCommandAdapter:
    return RosCommandAdapter(
        name=name,
        log_label=log_label,
        cwd=cwd,
        setup_script=f"{cwd}/devel/setup.bash",
        cmd="roslaunch arm_control L5.launch" if name != "roscore" else "roscore",
        path_prefix=_CONDA_BIN,
    )


@register("robot-cr1")
def _profile() -> RobotProfile:
    return RobotProfile(
        robot_name="robot-cr1",
        display_name="CR1",
        adapters=(
            _ros_adapter("roscore", "SYS_PREP", f"{_ROBOTIC_ARM_WS}/follow1"),
            _ros_adapter("ros_master1", "ROS_MASTER1", f"{_ROBOTIC_ARM_WS}/master1"),
            _ros_adapter("ros_pos_follow1", "ROS_POS_FOLLOW1", f"{_ROBOTIC_ARM_WS}/pos_follow1"),
            _ros_adapter("ros_master2", "ROS_MASTER2", f"{_ROBOTIC_ARM_WS}/master2"),
            _ros_adapter("ros_pos_follow2", "ROS_POS_FOLLOW2", f"{_ROBOTIC_ARM_WS}/pos_follow2"),
            CanAdapter(device_indices=(0, 1, 2, 3, 4)),
            RobotOsAdapter(),
            ApiAdapter(),
        ),
        flow_factory=build_cr1_flow,
        ros_required=True,
        can_required=True,
        launch_modes=("bilateral",),
        extra={
            "required_paths": tuple(
                item
                for name, cwd in _ROS_NODE_CWDS
                for item in (
                    ("dir", cwd, f"CR1 {name} 工作目录不存在: {cwd}"),
                    (
                        "file",
                        f"{cwd}/devel/setup.bash",
                        f"CR1 {name} setup 脚本不存在: {cwd}/devel/setup.bash",
                    ),
                )
            )
        },
    )
