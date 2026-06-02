import pdb
import os
import torch
import tiktoken
import numpy as np
import random
from typing import Any, List
from PIL import Image
from torchvision import transforms as T
from dataclasses import dataclass, field

import sys

root_dir = os.path.abspath("../..")
if root_dir not in sys.path:
    sys.path.append(root_dir)
from data.schema.dataloader import (
    AInnoRobotDatasets,
    ainno_robot_datasets_dataloader,
    AInnoDatasetConfig,
)


@dataclass
class ProcessorConfig:
    use_language: bool = True
    tokenizer: Any = tiktoken.get_encoding("cl100k_base")
    max_text_token_length: int = 32
    padding_token: int = 100257
    vacab_size: int = 100258
    camera_keys: List[str] = field(
        default_factory=lambda: ["camera5", "camera2", "camera3"]
    )
    image_size: List[int] = field(default_factory=lambda: [256, 256])
    image_augment: bool = True
    use_depth: bool = True
    use_proprio: bool = True
    base_state_dim: int = 0
    lift_state_dim: int = 0
    arm1_eef_state_dim: int = 6
    arm1_gripper_state_dim: int = 3
    base_action_dim: int = 0
    lift_action_dim: int = 0
    arm1_eef_action_dim: int = 6
    arm1_gripper_action_dim: int = 1
    stop_action_dim: int = 1
    observation_window_size: int = 1
    action_chunk_size: int = 10
    # downsample_rate: int = 10
    eos_steps: int = 1
    stop_action_thresh: float = 0.0001
    # cut_start_frame_range: int = 0
    max_act: List[float] = field(default_factory=lambda: [1.0])
    min_act: List[float] = field(default_factory=lambda: [-1.0])
    max_state: List[float] = field(default_factory=lambda: [1.0])
    min_state: List[float] = field(default_factory=lambda: [-1.0])


class AixerProcessor:
    def __init__(self, config):
        self.config = config
        self.text_tokenizer = config.tokenizer

        rgb_transform = [
            T.ToPILImage(),
            T.Resize(
                config.image_size,
                interpolation=Image.BICUBIC,
                max_size=None,
                antialias=True,
            ),
            T.ToTensor(),
            T.Normalize(
                mean=torch.tensor([0.485, 0.456, 0.406]),
                std=torch.tensor([0.229, 0.224, 0.225]),
            ),
        ]
        if config.image_augment:
            rgb_transform.insert(
                2, T.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2)
            )
        self.rgb_transform = T.Compose(rgb_transform)

        self.depth_transform = T.Compose(
            [T.ToPILImage(), T.Resize(config.image_size), T.ToTensor()]
        )
        assert not (
            config.max_act is None
            or config.min_act is None
            or config.max_state is None
            or config.min_state is None
        )
        state_dim = (
            config.base_state_dim
            + config.lift_state_dim
            + config.arm1_eef_state_dim
            + config.arm1_gripper_state_dim
        )
        action_dim = (
            config.base_action_dim
            + config.lift_action_dim
            + config.arm1_eef_action_dim
            + config.arm1_gripper_action_dim
            + config.stop_action_dim
        )
        assert (
            len(config.max_act) == action_dim
            and len(config.max_act) == action_dim
            and len(config.max_state) == state_dim
            and len(config.min_state) == state_dim
        )

        self.preprocessor = {
            "action": lambda x: (2 * x - (config.max_act + config.min_act))
            / (config.max_act - config.min_act),
            "proprio": lambda x: (2 * x - (config.max_state + config.min_state))
            / (config.max_state - config.min_state),
        }

        self.post_processer = lambda a: 0.5 * (
            a * (config.max_act - config.min_act) + (config.max_act + config.min_act)
        )

    def process(self, sample):
        def _pad_if_necessary(x, dim):
            if dim <= 0:
                return None
            if x is None:
                return torch.as_tensor([0] * dim)
            pad_num = dim - len(x)
            if pad_num > 0:
                x = np.concatenate([x, np.zeros(pad_num)])
            return torch.as_tensor(x)

        return_dict = dict()
        # 1. lang instructions
        if self.config.use_language:
            langs_texts = [
                lang["task_name"]
                + (
                    random.choice(lang["lang_instructions"])
                    if lang["lang_instructions"]
                    else ""
                )
                for lang in sample["lang_instructions"]
            ]
            lang_token_ids, lang_token_masks = [], []
            for lang in langs_texts:
                lang_ids = self.text_tokenizer.encode(lang)
                assert len(lang_ids) <= self.config.max_text_token_length, (
                    "The lang instruction is too long, you can enlarge the value of `max_text_token_length` to solve this problem. "
                    "It should be noted that you may also need to enlarge the value of `gpt_block_size` accordingly."
                )
                padding_num = self.config.max_text_token_length - len(lang_ids)
                lang_ids = (
                    lang_ids + [self.config.padding_token] * padding_num
                    if padding_num > 0
                    else lang_ids
                )
                lang_token_ids.append(lang_ids)
                lang_token_masks.append(
                    [0 if id == self.config.padding_token else 1 for id in lang_ids]
                )
            return_dict["lang_instructions"] = torch.as_tensor(
                lang_token_ids
            )  # shape=[H, max_text_token_length]
            return_dict["lang_instruction_masks"] = torch.as_tensor(
                lang_token_masks
            )  # shape=[H, max_text_token_length]

        # 2. cameras
        padding_image_shape = [
            self.config.observation_window_size,
            3,
            self.config.image_size[0],
            self.config.image_size[1],
        ]
        for camera in self.config.camera_keys:
            rgb_key = camera + "_rgb"
            depth_key = camera + "_depth"

            return_dict[rgb_key] = torch.as_tensor(
                np.stack(sample[rgb_key])
                if rgb_key in sample
                else np.zeros(padding_image_shape)
            )  # [H, 3, h, w]
            if self.config.use_depth:
                return_dict[depth_key] = torch.as_tensor(
                    np.stack(sample[depth_key])
                    if depth_key in sample
                    else np.zeros(padding_image_shape)
                )  # [H, 3, h, w]

        # 3. proprios
        if self.config.use_proprio:
            proprios = []
            for states in sample["states"]:
                proprio = [
                    _pad_if_necessary(
                        states["base_state"], dim=self.config.base_state_dim
                    ),
                    _pad_if_necessary(
                        states["lift_state"], dim=self.config.lift_state_dim
                    ),
                    _pad_if_necessary(
                        states["arm1_eef_state"], dim=self.config.arm1_eef_state_dim
                    ),
                    _pad_if_necessary(
                        states["arm1_gripper_state"],
                        dim=self.config.arm1_gripper_state_dim,
                    ),
                ]
                proprios.append(torch.cat([x for x in proprio if x is not None]))
            return_dict["proprioception"] = torch.stack(proprios)  # [H, d]

        # 4. actions
        targets = []
        for i, actions in enumerate(sample["actions"]):
            action = [
                _pad_if_necessary(
                    actions["base_action_delta"], self.config.base_action_dim
                ),
                _pad_if_necessary(
                    actions["lift_action_delta"], self.config.lift_action_dim
                ),
                _pad_if_necessary(
                    actions["arm1_eef_action_delta"], self.config.arm1_eef_action_dim
                ),
                _pad_if_necessary(
                    actions["arm1_gripper_action_abs"],
                    self.config.arm1_gripper_action_dim,
                ),
                _pad_if_necessary(actions["is_terminal"], self.config.stop_action_dim),
            ]
            targets.append(torch.cat([x for x in action if x is not None]))
        return_dict["actions"] = (
            torch.stack(targets)
            .unfold(0, self.config.action_chunk_size, 1)
            .transpose(1, 2)
        )  # [H, Q, d]

        return return_dict

    def collate_fn(self, samples):
        input_samples = list(map(self.process, samples))

        return_dict = dict()
        for key in input_samples[0].keys():
            return_dict[key] = torch.stack([sample[key] for sample in input_samples])

        return return_dict


if __name__ == "__main__":
    # 获取支持的所有数据集名称
    all_dataset_names = AInnoRobotDatasets.get_all_dataset_names()
    print("all_dataset_names: ", all_dataset_names)
    # (Pdb++) all_dataset_names
    # ['realman', 'droid', 'taco_play']

    # 自定义processor
    processor = AixerProcessor(
        ProcessorConfig(
            max_act=[1.0] * 8,
            min_act=[-1.0] * 8,
            max_state=[1.0] * 9,
            min_state=[-1.0] * 9,
        )
    )

    datasets_mixture = {
        "realman": 1.0,
        # "droid": 0.3,
        # "taco_play": 1.5
    }

    config = AInnoDatasetConfig(
        observation_window_size=processor.config.observation_window_size,
        action_chunk_size=processor.config.action_chunk_size,
        downsample_rate=10,
        randomly_cut_leading_steps_less_than=5,
    )

    datasets = AInnoRobotDatasets(
        config, datasets_mixture=datasets_mixture, train_ratio=0.9
    )

    train_dataloader = ainno_robot_datasets_dataloader(
        datasets,
        split="train",
        batchsize=16,
        shuffle=True,
        collate_fn=processor.collate_fn,
    )
    eval_dataloader = ainno_robot_datasets_dataloader(
        datasets,
        split="val",
        batchsize=16,
        shuffle=False,
        collate_fn=processor.collate_fn,
    )

    # 获取当前使用的所有数据集名称
    used_dataset_names = datasets.get_used_dataset_names()
    print("used_dataset_names: ", used_dataset_names)
    # (Pdb++) used_dataset_names
    # ['realman']

    # 获取所有用于训练的episode文件路径
    train_episodes = datasets.get_train_episodes()
    print(f"train_episodes: number {len(train_episodes)}, example: {train_episodes[0]}")
    # (Pdb++) len(train_episodes), train_episodes[0]
    # 2709, /mnt/nas03/ainno_robot_datasets/AInnoRobotDatasets/single_arm/realman20241016/20240906104743_realman_Aixier_工业_创新奇智研发实验室_把小刀放到黄色框里_758.json

    # 获取所有用于验证的episode文件路径
    val_episodes = datasets.get_val_episodes()
    print(f"val_episodes: number {len(val_episodes)}, example: {val_episodes[0]}")
    # (Pdb++) len(val_episodes), val_episodes[0]
    # 301, /mnt/nas03/ainno_robot_datasets/AInnoRobotDatasets/single_arm/realman20241016/20240906162212_realman_Aixier_工业_创新奇智研发实验室_把电源线放到黄色框里_1072.json

    # 获取所有的episode文件路径
    all_episodes = datasets.get_all_episodes()
    print(f"all_episodes: number {len(all_episodes)}, example: {all_episodes[0]}")
    # (Pdb++) len(all_episodes), all_episodes[0]
    # 3010, /mnt/nas03/ainno_robot_datasets/AInnoRobotDatasets/single_arm/realman20241016/20240906104743_realman_Aixier_工业_创新奇智研发实验室_把小刀放到黄色框里_758.json

    # 获取数据集的样本数量
    # num_samples = datasets.num_samples
    # print("num_samples: ", num_samples)
    # (Pdb++) num_samples
    # {'train': 2263717, 'val': 247025, 'all_origin': 2510742}

    for step, batch in enumerate(train_dataloader):
        if step >= 2:  # only print 2 steps data for anybody's quick reference
            break
        for key in batch:
            batch[key] = batch[key].to("cuda")
            print(key, batch[key].shape)
            break

        # model forward ...
