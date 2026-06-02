import numpy as np
import os
import sys
import time

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.append(root_dir)
from data.schema.episode_dataclass import (
    Episode,
    Step,
    Observation,
    Metadata,
    Action,
    CameraFrame,
)

# Creating a dummy Episode object
dummy_episode = Episode(
    metadata=Metadata(
        dataset_name="test_dataset",
        episode_id=1,
        experiment_time="2024-07-15T10:00:00",
        operator="Test Operator",
        scene="Test Scene",
        environment="Test Environment",
        task_name="Test Task",
        lang_instructions=["Move to the target", "Please move to the target"],
        goal_image=np.random.randint(0, 255, (64, 64, 3)).tolist(),
        goal_depth=np.random.randint(0, 255, (64, 64)).tolist(),
        sample_rate=30,
        num_steps=10,
        robot_name="Test Robot",
        robot_type="single_arm",
        robot_description="Test Description",
        robot_arm1_joints_state_dim=6,
        robot_arm2_joints_state_dim=6,
        robot_arm1_eef_state_dim=6,
        robot_arm2_eef_state_dim=6,
        robot_arm1_gripper_state_dim=3,
        robot_arm2_gripper_state_dim=3,
        robot_master_arm1_joints_state_dim=6,
        robot_master_arm2_joints_state_dim=6,
        robot_master_arm1_eef_state_dim=6,
        robot_master_arm2_eef_state_dim=6,
        robot_master_arm1_gripper_state_dim=3,
        robot_master_arm2_gripper_state_dim=3,
        robot_lift_state_dim=3,
        robot_base_state_dim=3,
        robot_arm1_joints_action_dim=6,
        robot_arm2_joints_action_dim=6,
        robot_arm1_eef_action_dim=6,
        robot_arm2_eef_action_dim=6,
        robot_arm1_gripper_action_dim=1,
        robot_arm2_gripper_action_dim=1,
        robot_lift_action_dim=3,
        robot_base_action_dim=3,
        camera1_rgb_resolution=[480, 640],
        camera2_rgb_resolution=[480, 640],
        camera1_depth_resolution=[480, 640],
        camera2_depth_resolution=[480, 640],
    ),
    steps=[
        Step(
            observation=Observation(
                arm1_joints_state=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                arm2_joints_state=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                arm1_eef_state=[0.1, 0.2, 0.3, 0.1, 0.2, 0.3],
                arm2_eef_state=[0.1, 0.2, 0.3, 0.1, 0.2, 0.3],
                arm1_gripper_state=[0.1, 0.2, 0.3],
                arm2_gripper_state=[0.1, 0.2, 0.3],
                master_arm1_joints_state=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                master_arm2_joints_state=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                master_arm1_eef_state=[0.1, 0.2, 0.3, 0.1, 0.2, 0.3],
                master_arm2_eef_state=[0.1, 0.2, 0.3, 0.1, 0.2, 0.3],
                master_arm1_gripper_state=[0.1, 0.2, 0.3],
                master_arm2_gripper_state=[0.1, 0.2, 0.3],
                lift_state=[0.1, 0.2, 0.3],
                base_state=[0.1, 0.2, 0.3],
                camera_frame=CameraFrame(
                    camera1_rgb=np.random.random((480, 640, 3)),
                    camera2_rgb=np.random.random((480, 640, 3)),
                    camera1_depth=np.random.randint(0, 2**16 - 1, (480, 640)),
                    camera2_depth=np.random.randint(0, 2**16 - 1, (480, 640)),
                ),
                timestamp=int(time.time() * 1000),
            ),
            action=Action(
                arm1_joints_action_delta=[0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                arm2_joints_action_delta=[0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                arm1_eef_action_delta=[0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                arm2_eef_action_delta=[0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                arm1_gripper_action_delta=[0.1],
                arm2_gripper_action_delta=[0.1],
                lift_action_delta=[0.1, 0.1, 0.1],
                base_action_delta=[0.1, 0.1, 0.1],
                is_terminal=False,
            ),
        )
        for _ in range(2)
    ],
)

# Dumping to JSON
episode_json = dummy_episode.model_dump_json(indent=4)

# Loading from JSON
new_episode = Episode.model_validate_json(episode_json)

# Verifying the data
print(episode_json)
print(new_episode)

import glob

filepath = next(
    glob.iglob("/mnt/nas03/ainno_robot_datasets/AInnoRobotDatasets/dual_arm/*/*.json")
)

episode = Episode.load(filepath)
print(episode)
