import pdb
import re
import time
import os
import cv2
import glob
import json
import math
import torch
import random
import numpy as np
from dataclasses import dataclass
from torch.utils.data import IterableDataset, Dataset

from .episode_dataclass import Episode
from .dataset_config import DATASET_NAME_PATH_PAIRS


@dataclass
class AInnoDatasetConfig:
    observation_window_size: int = 1  # 取多少个时间步的observation，包含当前step
    action_chunk_size: int = (
        1  # 取多少个时间步的action,包含当前step，用于模型预测未来一段时间步的action.
    )
    downsample_rate: int = (
        1  # 取action时，将若干时间步的(delta) action累积，用于模型直接预测downsample_rate这么多时间步之后的action
    )
    randomly_cut_leading_steps_less_than: int = (
        0  # 在这个范围内，随机将episode开始的一段时间步数据去除，原因是episode开始一小段时间的操作可能是停止或其它非理想状态。
    )


class AInnoDataset(IterableDataset):

    def __init__(
        self,
        file_list,
        config: AInnoDatasetConfig,
        shuffle=False,
        load_image=True,
    ):
        self.file_list = file_list
        self.config = config
        self.shuffle = shuffle
        self.observation_window_size = config.observation_window_size
        self.action_chunk_size = config.action_chunk_size
        self.downsample_rate = config.downsample_rate
        self._load_image = load_image

    def __iter__(self):
        worker_info = torch.utils.data.get_worker_info()
        if worker_info:
            num_workers = worker_info.num_workers
            worker_id = worker_info.id
        else:
            num_workers = 1
            worker_id = 0

        iter_files = self.file_list[worker_id::num_workers]
        file_indexes = list(range(len(iter_files)))
        if self.shuffle:
            random.shuffle(iter_files)

        num_episode_parallel = 10
        num_episode_batch = math.ceil(len(iter_files) / num_episode_parallel)
        for i in range(num_episode_batch):
            buffered_episode_data = []
            step_data_indexes = []
            for j in range(num_episode_parallel):
                if len(file_indexes) == 0:
                    break
                file_index = file_indexes.pop(0)
                filename = iter_files[file_index]
                episode_data, num_steps = self.get_episode_data(filename)
                buffered_episode_data.append(episode_data)
                step_data_indexes.extend([(j, k) for k in range(num_steps)])

            # random select
            if self.shuffle:
                random.shuffle(step_data_indexes)
            for idx in step_data_indexes:
                yield self.get_input_sample(buffered_episode_data[idx[0]], idx[1])

    def get_episode_data(self, filename):
        episode_data = dict()
        with open(filename, "rb") as rf:
            episode_json = json.load(rf)
            data = Episode.model_validate_json(json.dumps(episode_json))
            num_steps = data.metadata.num_steps

            # cutoff random number of frames at the begin
            start_step_index = 0

            if self.config.randomly_cut_leading_steps_less_than > 0:
                start_step_index = random.randint(
                    0, self.config.randomly_cut_leading_steps_less_than
                )
                start_step_index = min(start_step_index, data.metadata.num_steps - 1)

            # 1.get camera data
            if self._load_image:
                camera_keys = []
                for attr_name, attr_value in data.metadata.__dict__.items():
                    if (
                        re.match("camera[0-9]_rgb_resolution", attr_name)
                        and attr_value is not None
                    ):
                        camera_keys.append(attr_name.rsplit("_", 1)[0])
                filepath_base = filename.split(".")[0]
                for camera in camera_keys:
                    filepath = filepath_base + f"_{camera}.mp4"
                    assert os.path.exists(filepath), f"File {filepath} does not found!"
                    frames = self.read_video_frames(filepath)
                    assert (
                        len(frames) == data.metadata.num_steps
                    ), f"The video {filepath} does not have enough frames. need: {data.metadata.num_steps}, found: {len(frames)}"
                    episode_data[camera] = frames[start_step_index:]

            # 2.get states数据
            states, actions, lang_instructions = self.get_step_data(data)
            states = states[start_step_index:]
            actions = actions[start_step_index:]
            lang_instructions = lang_instructions[start_step_index:]
            episode_data["states"] = states
            episode_data["lang_instructions"] = lang_instructions
            episode_data["actions"] = actions
            if self.observation_window_size > 1:
                for k, v in episode_data.items():
                    episode_data[k] = v + [v[-1]] * (self.observation_window_size - 1)
            episode_data["metadata"] = {"dataset_name": data.metadata.dataset_name}
        return episode_data, num_steps - start_step_index

    def downsample_actions(self, actions):
        # downsample: step数量不减少，但是每个step的action应该是后续多个step的action之和
        num_steps = len(actions)

        new_actions = []
        for idx in range(num_steps):
            sub_action_indexes = range(idx, min(idx + self.downsample_rate, num_steps))
            new_action = dict()
            for key in actions[0].keys():
                values = [actions[i][key] for i in sub_action_indexes]
                if values[0] is None:
                    new_action[key] = None
                else:
                    new_action[key] = np.asanyarray(values).sum(axis=0)
                    if "gripper" in key:
                        new_action[key] = values[0]  # 0-close, 1-open
            new_actions.append(new_action)
        return new_actions

    def get_step_data(self, data):
        dataset_name = data.metadata.dataset_name
        all_states, all_actions, all_lang_instructions = [], [], []
        task_name = data.metadata.task_name
        for idx in range(data.metadata.num_steps):
            states = self.get_step_state(data.steps[idx])
            lang_instructions = data.metadata.lang_instructions
            all_states.append(states)
            all_lang_instructions.append(
                dict(
                    lang_instructions=lang_instructions,
                    task_name=task_name,
                )
            )

            actions = self.get_step_action(data.steps[idx])
            all_actions.append(actions)

        # downsample
        all_actions = self.downsample_actions(all_actions)

        return all_states, all_actions, all_lang_instructions

    def get_step_action(self, step):

        def _array(x):
            return (
                (np.array(x) if isinstance(x, list) else np.array([x]))
                if x is not None
                else None
            )

        action = dict()
        action["base_action_delta"] = _array(step.action.base_action_delta)
        action["lift_action_delta"] = _array(step.action.lift_action_delta)
        action["arm1_joints_action_delta"] = _array(
            step.action.arm1_joints_action_delta
        )
        action["arm2_joints_action_delta"] = _array(
            step.action.arm2_joints_action_delta
        )
        action["arm1_eef_action_delta"] = _array(step.action.arm1_eef_action_delta)
        action["arm2_eef_action_delta"] = _array(step.action.arm2_eef_action_delta)
        action["arm1_joints_action_abs"] = _array(step.action.arm1_joints_action_abs)
        action["arm2_joints_action_abs"] = _array(step.action.arm2_joints_action_abs)
        action["arm1_eef_action_abs"] = _array(step.action.arm1_eef_action_abs)
        action["arm2_eef_action_abs"] = _array(step.action.arm2_eef_action_abs)
        action["arm1_gripper_action_abs"] = _array(step.action.arm1_gripper_action_abs)
        action["arm2_gripper_action_abs"] = _array(step.action.arm2_gripper_action_abs)
        action["is_terminal"] = _array(int(step.action.is_terminal))
        return action

    def get_step_state(self, step):

        def _array(x):
            return (
                (np.array(x) if isinstance(x, list) else np.array([x]))
                if x is not None
                else None
            )

        states = dict()
        states["base_state"] = _array(step.observation.base_state)
        states["lift_state"] = _array(step.observation.lift_state)
        states["arm1_joints_state"] = _array(step.observation.arm1_joints_state)
        states["arm1_eef_state"] = _array(step.observation.arm1_eef_state)
        states["arm1_gripper_state"] = _array(step.observation.arm1_gripper_state)
        states["arm2_joints_state"] = _array(step.observation.arm2_joints_state)
        states["arm2_eef_state"] = _array(step.observation.arm2_eef_state)
        states["arm2_gripper_state"] = _array(step.observation.arm2_gripper_state)
        states["master_arm1_joints_state"] = _array(
            step.observation.master_arm1_joints_state
        )
        states["master_arm1_eef_state"] = _array(step.observation.master_arm1_eef_state)
        states["master_arm1_gripper_state"] = _array(
            step.observation.master_arm1_gripper_state
        )
        states["master_arm2_joints_state"] = _array(
            step.observation.master_arm2_joints_state
        )
        states["master_arm2_eef_state"] = _array(step.observation.master_arm2_eef_state)
        states["master_arm2_gripper_state"] = _array(
            step.observation.master_arm2_gripper_state
        )
        return states

    def read_video_frames(self, filepath):
        frames = []
        video = cv2.VideoCapture(filepath)
        success, frame = video.read()
        while success:
            frame = frame[:, :, ::-1]  # BGR2RGB, shape = [H, W， C]
            frames.append(frame)
            success, frame = video.read()
        video.release()
        return frames

    def get_input_sample(self, episode_data, idx):
        return_dict = dict()

        for key, data in episode_data.items():
            if key == "actions":
                num_steps = self.observation_window_size + self.action_chunk_size - 1
                sampled_data = data[idx : idx + num_steps]
                if len(sampled_data) < num_steps:
                    sampled_data = sampled_data + [sampled_data[-1]] * (
                        num_steps - len(sampled_data)
                    )
                return_dict[key] = sampled_data  # [H', ...]
            elif key in {"metadata"}:
                return_dict[key] = {
                    **data,
                    "step_index": idx,
                }
            else:
                return_dict[key] = data[
                    idx : idx + self.observation_window_size
                ]  # [H, ...]
        return return_dict


class AInnoRobotDatasets:
    def __init__(
        self, config: AInnoDatasetConfig, datasets_mixture=None, train_ratio=0.9
    ):
        self.config = config
        self.datasets_mixture = datasets_mixture
        self.all_dataset_names = self.get_all_dataset_names()
        self.dataset_names = (
            list(datasets_mixture.keys())
            if datasets_mixture is not None
            else self.all_dataset_names
        )
        self.episode_files = self.get_episode_files(train_ratio)
        self.train_datasets = AInnoDataset(self.episode_files["train"], config)
        self.val_datasets = AInnoDataset(self.episode_files["val"], config)
        self.all_datasets = AInnoDataset(self.episode_files["all"], config)

    def get_episode_files(self, train_ratio, shuffle=True):
        train_episode_files, eval_episode_files = [], []
        for dataset_name in self.dataset_names:
            root_dirs = DATASET_NAME_PATH_PAIRS.get(dataset_name, None)
            assert root_dirs is not None, f"{dataset_name} is not supported currently."
            if not isinstance(root_dirs, list):
                root_dirs = [root_dirs]
            episode_files = []
            for root_dir in root_dirs:
                if root_dir is None or not os.path.exists(root_dir):
                    raise ValueError(
                        f" ERROR: Dataset {dataset_name} is not supported or data root path is not correct."
                    )
                episode_files.extend(list(glob.glob(os.path.join(root_dir, "*.json"))))
            assert (
                len(episode_files) > 0
            ), f"Dataset {dataset_name} has no episode found."

            if shuffle:
                random.seed(9527)
                random.shuffle(episode_files)

            # 划分训练集和验证集
            num_train = round(len(episode_files) * train_ratio)
            train_episodes = episode_files[:num_train]
            val_episodes = episode_files[num_train:]

            if self.datasets_mixture.get(dataset_name, None) is not None:
                train_episodes = self.sample_episodes(
                    train_episodes, self.datasets_mixture.get(dataset_name, 1.0)
                )

            train_episode_files.extend(train_episodes)
            eval_episode_files.extend(val_episodes)
        return {
            "train": train_episode_files,
            "val": eval_episode_files,
            "all": train_episode_files + eval_episode_files,
        }

    def sample_episodes(self, origin_files, weight):
        target_number = round(len(origin_files) * weight)
        if target_number == len(origin_files):
            return origin_files
        elif target_number < len(origin_files):
            indexes = np.random.choice(
                np.array(list(range(len(origin_files)))),
                size=target_number,
                replace=False,
            )
            indexes.sort()
            return [origin_files[index] for index in indexes]
        else:
            a, b = divmod(target_number, len(origin_files))
            sampled_files = origin_files * a
            if b > 0:
                indexes = np.random.choice(
                    np.array(list(range(len(origin_files)))), size=b, replace=False
                )
                indexes.sort()
                sampled_files.extend([origin_files[index] for index in indexes])
            return sampled_files

    @staticmethod
    def get_dataset_episode_filepaths(dataset_name):
        if isinstance(dataset_name, str):
            dataset_names = [dataset_name]
        elif isinstance(dataset_name, list):
            dataset_names = dataset_name
        else:
            raise ValueError("arg should be str or list of str")
        episode_filepaths = []
        for dn in dataset_names:
            dataset_dirs = DATASET_NAME_PATH_PAIRS[dn]
            dataset_dirs = (
                [dataset_dirs] if not isinstance(dataset_dirs, list) else dataset_dirs
            )
            for dataset_dir in dataset_dirs:
                episode_filepaths.extend(
                    list(glob.glob(os.path.join(dataset_dir, "*.json")))
                )
        return episode_filepaths

    @staticmethod
    def get_all_dataset_names():
        """获取支持的所有数据集名称"""
        return list(DATASET_NAME_PATH_PAIRS.keys())

    def get_used_dataset_names(self):
        """获取使用的数据集名称"""
        return self.dataset_names

    def get_train_episodes(self):
        return list(set(self.episode_files["train"]))

    def get_val_episodes(self):
        return list(set(self.episode_files["val"]))

    def get_all_episodes(self):
        return list(set(self.episode_files["all"]))


def ainno_robot_datasets_dataloader(
    ainno_robot_datasets,
    split="all",
    batchsize=8,
    num_workers=0,
    prefetch_factor=None,
    drop_last=False,
    shuffle=True,
    collate_fn=None,
):
    if num_workers > 0 and prefetch_factor is None:
        prefetch_factor = 2
    if num_workers == 0 and prefetch_factor is not None:
        prefetch_factor = None
    if split == "train":
        datasets = ainno_robot_datasets.train_datasets
    elif split == "val":
        datasets = ainno_robot_datasets.val_datasets
    elif split == "all":
        datasets = ainno_robot_datasets.all_datasets
    else:
        raise ValueError(f"split error. only `train`, `val` and `all` are supported.")
    datasets.shuffle = shuffle
    return torch.utils.data.DataLoader(
        datasets,
        batch_size=batchsize,
        num_workers=num_workers,
        prefetch_factor=prefetch_factor,
        drop_last=drop_last,
        collate_fn=collate_fn,
        pin_memory=True,
    )
