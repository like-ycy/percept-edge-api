from typing import List, Optional

from pydantic import BaseModel


class Metadata(BaseModel):
    experiment_time: Optional[str] = None
    operator: Optional[str] = None
    task_name: Optional[str] = None
    lang_instructions: Optional[List[str]] = None
    sample_rate: Optional[int] = None
    num_steps: Optional[int] = None
    robot_name: Optional[str] = None
    robot_type: Optional[str] = None
    robot_description: Optional[str] = None


class Observation(BaseModel):
    # 机械臂关节位置
    arm_left_joints_state: Optional[List[float]] = None
    arm_right_joints_state: Optional[List[float]] = None

    # 机械臂电流
    arm_left_current_state: Optional[List[float]] = None
    arm_right_current_state: Optional[List[float]] = None

    # 机械臂速度
    arm_left_velocity_state: Optional[List[float]] = None
    arm_right_velocity_state: Optional[List[float]] = None

    # 末端执行器位置
    arm_left_eef_state: Optional[List[float]] = None
    arm_right_eef_state: Optional[List[float]] = None

    # 夹爪位置
    gripper_left_state: Optional[List[float]] = None
    gripper_right_state: Optional[List[float]] = None

    # 吸盘位置
    sucker_left_state: Optional[List[float]] = None
    sucker_right_state: Optional[List[float]] = None

    # 主臂关节位置
    master_arm_left_joints_state: Optional[List[float]] = None
    master_arm_right_joints_state: Optional[List[float]] = None

    # 主臂电流
    master_arm_left_current_state: Optional[List[float]] = None
    master_arm_right_current_state: Optional[List[float]] = None

    # 主臂速度
    master_arm_left_velocity_state: Optional[List[float]] = None
    master_arm_right_velocity_state: Optional[List[float]] = None

    # 主臂末端执行器位置
    master_arm_left_eef_state: Optional[List[float]] = None
    master_arm_right_eef_state: Optional[List[float]] = None

    # 主臂夹爪位置
    master_gripper_left_state: Optional[List[float]] = None
    master_gripper_right_state: Optional[List[float]] = None

    # 躯干关节位置
    torso_joints_state: Optional[List[float]] = None

    # 躯干电流
    torso_current_state: Optional[List[float]] = None

    # 躯干速度
    torso_velocity_state: Optional[List[float]] = None

    # 手部关节位置
    hand_left_joints_state: Optional[List[float]] = None
    hand_right_joints_state: Optional[List[float]] = None

    # 手部力状态
    hand_left_force_state: Optional[List[float]] = None
    hand_right_force_state: Optional[List[float]] = None

    # 手部速度
    hand_left_velocity_state: Optional[List[float]] = None
    hand_right_velocity_state: Optional[List[float]] = None

    # 手部电流
    hand_left_current_state: Optional[List[float]] = None
    hand_right_current_state: Optional[List[float]] = None

    # 头部关节
    head_joints_state: Optional[List[float]] = None

    # 头部电流
    head_current_state: Optional[List[float]] = None

    # 头部速度
    head_velocity_state: Optional[List[float]] = None

    # 吸附状态
    adsorption_state: Optional[List[float]] = None

    # 平移状态
    translation_state: Optional[List[float]] = None

    # 升降状态
    lift_state: Optional[List[float]] = None

    # 底盘状态
    base_state: Optional[List[float]] = None

    # VR 末端执行器位置
    vr_head_eef_state: Optional[List[float]] = None
    vr_hand_left_eef_state: Optional[List[float]] = None
    vr_hand_right_eef_state: Optional[List[float]] = None

    timestamp: Optional[int] = None


class Action(BaseModel):
    arm_left_joints_action_abs: Optional[List[float]] = None
    arm_right_joints_action_abs: Optional[List[float]] = None
    arm_left_eef_action_abs: Optional[List[float]] = None
    arm_right_eef_action_abs: Optional[List[float]] = None
    gripper_left_action_abs: Optional[List[float]] = None
    gripper_right_action_abs: Optional[List[float]] = None
    sucker_left_action_abs: Optional[List[float]] = None
    sucker_right_action_abs: Optional[List[float]] = None
    torso_joints_action_abs: Optional[List[float]] = None
    hand_left_joints_action_abs: Optional[List[float]] = None
    hand_right_joints_action_abs: Optional[List[float]] = None
    head_joints_action_abs: Optional[List[float]] = None
    adsorption_action_abs: Optional[List[float]] = None
    translation_action_abs: Optional[List[float]] = None
    lift_action_abs: Optional[List[float]] = None
    base_action_abs: Optional[List[float]] = None
    is_terminal: Optional[bool] = None


class Step(BaseModel):
    observation: Optional[Observation] = None
    action: Optional[Action] = None


class Episode(BaseModel):
    metadata: Optional[Metadata] = None
    steps: Optional[List[Step]] = None
