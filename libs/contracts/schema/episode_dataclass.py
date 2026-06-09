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
    # Arm joints position
    arm_left_joints_state: Optional[List[float]] = None
    arm_right_joints_state: Optional[List[float]] = None

    # Arm current
    arm_left_current_state: Optional[List[float]] = None
    arm_right_current_state: Optional[List[float]] = None

    # Arm velocity
    arm_left_velocity_state: Optional[List[float]] = None
    arm_right_velocity_state: Optional[List[float]] = None

    # End effector position
    arm_left_eef_state: Optional[List[float]] = None
    arm_right_eef_state: Optional[List[float]] = None

    # Gripper position
    gripper_left_state: Optional[List[float]] = None
    gripper_right_state: Optional[List[float]] = None

    # Sucker position
    sucker_left_state: Optional[List[float]] = None
    sucker_right_state: Optional[List[float]] = None

    # Master arm joints position
    master_arm_left_joints_state: Optional[List[float]] = None
    master_arm_right_joints_state: Optional[List[float]] = None

    # Master arm current
    master_arm_left_current_state: Optional[List[float]] = None
    master_arm_right_current_state: Optional[List[float]] = None

    # Master arm velocity
    master_arm_left_velocity_state: Optional[List[float]] = None
    master_arm_right_velocity_state: Optional[List[float]] = None

    # Master arm end effector position
    master_arm_left_eef_state: Optional[List[float]] = None
    master_arm_right_eef_state: Optional[List[float]] = None

    # Master arm gripper position
    master_gripper_left_state: Optional[List[float]] = None
    master_gripper_right_state: Optional[List[float]] = None

    # Torso joints position
    torso_joints_state: Optional[List[float]] = None

    # Torso current
    torso_current_state: Optional[List[float]] = None

    # Torso velocity
    torso_velocity_state: Optional[List[float]] = None

    # Hand joints position
    hand_left_joints_state: Optional[List[float]] = None
    hand_right_joints_state: Optional[List[float]] = None

    # Hand force
    hand_left_force_state: Optional[List[float]] = None
    hand_right_force_state: Optional[List[float]] = None

    # Hand velocity
    hand_left_velocity_state: Optional[List[float]] = None
    hand_right_velocity_state: Optional[List[float]] = None

    # Hand current
    hand_left_current_state: Optional[List[float]] = None
    hand_right_current_state: Optional[List[float]] = None

    # Head joints
    head_joints_state: Optional[List[float]] = None

    # Head current
    head_current_state: Optional[List[float]] = None

    # Head velocity
    head_velocity_state: Optional[List[float]] = None

    # Adsorption state
    adsorption_state: Optional[List[float]] = None

    # Translation state
    translation_state: Optional[List[float]] = None

    # Lift state
    lift_state: Optional[List[float]] = None

    # Base state
    base_state: Optional[List[float]] = None

    # vr end effector position
    vr_head_eef_state: Optional[List[float]] = None
    vr_hand_left_eef_state: Optional[List[float]] = None
    vr_hand_right_eef_state: Optional[List[float]] = None

    timestamp: Optional[int] = None


class Action(BaseModel):
    # Arm joints action delta
    arm_left_joints_action_delta: Optional[List[float]] = None
    arm_right_joints_action_delta: Optional[List[float]] = None

    # Arm joints action absolute
    arm_left_joints_action_abs: Optional[List[float]] = None
    arm_right_joints_action_abs: Optional[List[float]] = None

    # Arm joints current action delta
    arm_left_current_action_delta: Optional[List[float]] = None
    arm_right_current_action_delta: Optional[List[float]] = None

    # Arm joints current action absolute
    arm_left_current_action_abs: Optional[List[float]] = None
    arm_right_current_action_abs: Optional[List[float]] = None

    # Arm joints velocity action delta
    arm_left_velocity_action_delta: Optional[List[float]] = None
    arm_right_velocity_action_delta: Optional[List[float]] = None

    # Arm joints velocity action absolute
    arm_left_velocity_action_abs: Optional[List[float]] = None
    arm_right_velocity_action_abs: Optional[List[float]] = None

    # End effector action delta
    arm_left_eef_action_delta: Optional[List[float]] = None
    arm_right_eef_action_delta: Optional[List[float]] = None

    # End effector action absolute
    arm_left_eef_action_abs: Optional[List[float]] = None
    arm_right_eef_action_abs: Optional[List[float]] = None

    # Lift action delta
    lift_action_delta: Optional[List[float]] = None

    # Lift action absolute
    lift_action_abs: Optional[List[float]] = None

    # Gripper action delta
    gripper_left_action_delta: Optional[List[float]] = None
    gripper_right_action_delta: Optional[List[float]] = None

    # Gripper action absolute
    gripper_left_action_abs: Optional[List[float]] = None
    gripper_right_action_abs: Optional[List[float]] = None

    # Sucker action delta
    sucker_left_action_delta: Optional[List[float]] = None
    sucker_right_action_delta: Optional[List[float]] = None

    # Sucker action absolute
    sucker_left_action_abs: Optional[List[float]] = None
    sucker_right_action_abs: Optional[List[float]] = None

    # Base action delta
    base_action_delta: Optional[List[float]] = None

    # Base action absolute
    base_action_abs: Optional[List[float]] = None

    # Torso joints action delta
    torso_joints_action_delta: Optional[List[float]] = None

    # Torso joints action absolute
    torso_joints_action_abs: Optional[List[float]] = None

    # Torso joints current action delta
    torso_current_action_delta: Optional[List[float]] = None

    # Torso joints current action absolute
    torso_current_action_abs: Optional[List[float]] = None

    # Torso joints velocity action delta
    torso_velocity_action_delta: Optional[List[float]] = None

    # Torso joints velocity action absolute
    torso_velocity_action_abs: Optional[List[float]] = None

    # Hand joints action delta
    hand_left_joints_action_delta: Optional[List[float]] = None
    hand_right_joints_action_delta: Optional[List[float]] = None

    # Hand joints action absolute
    hand_left_joints_action_abs: Optional[List[float]] = None
    hand_right_joints_action_abs: Optional[List[float]] = None

    # Hand velocity action delta
    hand_left_velocity_action_delta: Optional[List[float]] = None
    hand_right_velocity_action_delta: Optional[List[float]] = None

    # Hand velocity action absolute
    hand_left_velocity_action_abs: Optional[List[float]] = None
    hand_right_velocity_action_abs: Optional[List[float]] = None

    # Hand current action delta
    hand_left_current_action_delta: Optional[List[float]] = None
    hand_right_current_action_delta: Optional[List[float]] = None

    # Hand current action absolute
    hand_left_current_action_abs: Optional[List[float]] = None
    hand_right_current_action_abs: Optional[List[float]] = None

    # Head joints action delta
    head_joints_action_delta: Optional[List[float]] = None

    # Head joints action absolute
    head_joints_action_abs: Optional[List[float]] = None

    # Head joints current action delta
    head_current_action_delta: Optional[List[float]] = None

    # Head joints current action absolute
    head_current_action_abs: Optional[List[float]] = None

    # Head joints velocity action delta
    head_velocity_action_delta: Optional[List[float]] = None

    # Head joints velocity action absolute
    head_velocity_action_abs: Optional[List[float]] = None

    # Hand force action delta
    hand_left_force_action_delta: Optional[List[float]] = None
    hand_right_force_action_delta: Optional[List[float]] = None

    # Hand force action absolute
    hand_left_force_action_abs: Optional[List[float]] = None
    hand_right_force_action_abs: Optional[List[float]] = None

    # Adsorption action delta
    adsorption_action_delta: Optional[List[float]] = None

    # Adsorption action absolute
    adsorption_action_abs: Optional[List[float]] = None

    # Translation action delta
    translation_action_delta: Optional[List[float]] = None

    # Translation action absolute
    translation_action_abs: Optional[List[float]] = None

    is_terminal: Optional[bool] = None


class Step(BaseModel):
    observation: Optional[Observation] = None
    action: Optional[Action] = None


class Episode(BaseModel):
    metadata: Optional[Metadata] = None
    steps: Optional[List[Step]] = None
