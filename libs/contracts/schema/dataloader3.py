import itertools
import os
import random
import numbers
import re
from tqdm import tqdm
from functools import lru_cache
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union, Any

import cv2
import ijson
import h5py
import numpy as np
import torch
import utils.logutil as logging
from torch.utils.data.datapipes.datapipe import IterDataPipe, MapDataPipe
from torch.utils.data.datapipes.iter import *
from torch.utils.data.datapipes.iter import IterableWrapper

from data.schema.episode_dataclass import *

def load_specific_frames_opencv(video_path: str, indices: Sequence[int]) -> List[np.ndarray]:
    """
    使用OpenCV高效加载视频中的指定的多个历史帧

    Args:
        video_path: 视频文件路径
        indices: 需要加载的帧索引序列

    Returns:
        按indices顺序排列的RGB格式numpy数组列表
    """

    try:
        cv2.setNumThreads(0)  # 降低并发避免线程冲突
        cap = cv2.VideoCapture(video_path)
        frames = []
        sorted_indices = sorted(set(indices)) # 去重并排序
        frame_dict = {}
        for fid in sorted_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fid)
            ret, frame = cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame_dict[fid] = frame
            else:
                raise ValueError(f"获取帧失败：video path ={video_path}, frame_id = {fid}")
        cap.release()
        # 按原始顺序返回结果
        frames = [frame_dict[fid] for fid in indices]
        return frames
    except Exception as e:
            logging.warning(f"加载视频帧失败: {video_path}, 错误: {str(e)}")
            raise e

def read_and_process_episode(json_path: str) -> Dict[str, Any]:
    """
    读取并处理单个episode

    Args:
        json_path: episode的JSON文件路径

    Returns:
        包含处理结果的字典，包含num_steps、json_path、metadata、steps、error等字段
    """
    # 一次性加载episode数据，不加载图像和深度以节省内存
    episode = Episode.load(json_path, load_image=False, load_depth=False)

    # 处理数据
    metadata = episode.metadata
    steps = restructure(episode)
    steps = infer_action(steps)

    return {
        "num_steps": len(steps),
        "json_path": json_path,
        "metadata": metadata,
        "steps": steps,
    }

def restructure(episode: Episode) -> List[Dict]:
    """
    Extract a list of steps from `Episode` obj.
    Each step in the output list is a dict with keys including "actions","states","images","tasks","metadata".
    将原本以episode为单位的数据结构转换为以时间步为单位的数据结构.

    Args:
        episode: Episode对象

    Returns:
        步骤列表，每个步骤包含actions、states、images、tasks、metadata等字段
    """
    steps = [
        dict(
            actions=step.action.dict(),
            states=step.observation.dict(),
            images=(
                step.observation.camera_frame.__dict__.copy()
                if step.observation.camera_frame
                else {}
            ),
            tasks=dict(
                language_instructions=episode.metadata.lang_instructions,
                task_name=episode.metadata.task_name,
            ),
            metadata=dict(
                time_step=i,
                dataset_name=episode.metadata.dataset_name,
                experiment_time=episode.metadata.experiment_time,
                scene=episode.metadata.scene,
                environment=episode.metadata.environment,
                robot_name=episode.metadata.robot_name,
                robot_type=episode.metadata.robot_type,
            ),
        )
        for i, step in enumerate(episode.steps)
    ]
    return steps
def sample_history(time_step: int,
                       history_obs_num: int = 5,
                       stride: int = 4,
                       mode: str="time_aware_v2") -> List[int]:
    """History sampling (返回的索引长度恒等于 history_obs_num，并在函数内完成 pad).

    args:
        time_step: 当前 step 的索引 (int)
        history_obs_num: 带history的观测帧数，默认为4，表示3帧历史+1帧当前观测（int）
        stride: uniform均匀采样下的步长、time_aware下的衰减因子、multi_scale下的粗尺度步长，默认为4（int）。
        mode: "uniform" | "random" | "time_aware" | "time_aware_v2" | "multi_scale"
        NOTE: 根据当前实验结果，直接将 history_obs_num 和 stride 默认设为4，无需传入，mode默认设置为time_aware_v2
        TODO: 后续可尝试不同的history_obs_num和stride配置、探索不同采样mode的影响
    """
    # ---- 基本检查 ----
    assert isinstance(time_step, numbers.Integral) and time_step >= 0, \
    f"time_step 必须是正整数，但实际是 {time_step} (类型: {type(time_step)})"
    assert mode in {"uniform", "random", "time_aware", "time_aware_v2","multi_scale"}, "未知采样模式"

    if mode == "uniform":
        indices = [
            max(0, time_step - idx * stride)
            for idx in range(history_obs_num)
        ][::-1]  # 升序：旧 -> 新

    elif mode == "random":
        candidates = list(range(0, time_step + 1))
        k = min(history_obs_num, len(candidates))
        indices = sorted(random.sample(candidates, k))

    elif mode == "time_aware":
        candidates = list(range(0, time_step + 1))
        # 越近权重越大：权重 ~ (stride)**-(i-idx)，但 stride=1 时会全1，仍可用
        weights = np.array([stride ** -(time_step - idx) for idx in candidates], dtype=float)
        denom = weights.sum()
        if denom <= 0:
            weights = np.ones_like(weights) / len(weights)
        else:
            weights = weights / denom
        k = min(history_obs_num, len(candidates))
        chosen = np.random.choice(candidates, k, replace=False, p=weights)
        indices = sorted(chosen.tolist())
    elif mode == "time_aware_v2":
        #与time_aware相比，采样到远处历史的概率更大
        candidates = np.arange(0, time_step + 1)
        alpha = 1.2  # 1.0~1.3 比较平滑；越大越偏近端
        weights = (time_step - candidates + 1).astype(float) ** (-alpha)
        weights = weights / weights.sum()
        k = min(history_obs_num, len(candidates))
        chosen = np.random.choice(candidates, k, replace=False, p=weights)
        indices = sorted(chosen.tolist())
    elif mode == "multi_scale":
        # 两种尺度合并，可能长度不足，最后 pad
        s = set()
        half = history_obs_num // 2
        # 细尺度：步长=1
        s.update([max(0, time_step - idx) for idx in range(half)])
        # 粗尺度：步长=2*stride
        s.update([max(0, time_step - idx * stride * 2) for idx in range(history_obs_num - half)])
        indices = sorted(s)
    else:
        raise ValueError(f"Unknown sampling mode: {mode}")

    while len(indices) < history_obs_num:
        last = indices[-1] if indices else time_step
        indices.append(last)

    # 兜底：强制长度 == history_obs_num（若多了，裁掉左侧，保留最近的）
    if len(indices) > history_obs_num:
        indices = indices[-history_obs_num:]
    # 强制包含当前步
    if indices[-1] != time_step:
        indices[-1] = time_step

    return indices

def chunk_act_obs(
    steps: Sequence[Mapping],
    action_chunk: int = 1,
    action_stride: int = 1,
    proprio_state_obs_window_size: int = 1,
    use_history: bool = False
) -> Sequence[Mapping]:
    """
    Chunks actions and observations.

    The terms "states," "images," and "tasks" are given a new history axis, resulting in a shape of [observation_window_size].
    The term "actions" is expanded with two new axes, giving it a shape of [observation_window_size, action_chunk].
    Additionally, "future_states" is introduced at each step, with a shape of [observation_window_size, action_chunk].
    This information is intended to be used as actions.

    The 'images' observation can have a different stride and window size than other state observations.

    Args:
        steps: 步骤序列
        action_chunk: 动作块大小
        action_stride: 动作步长
        use_history: 是否使用历史观测

    Returns:
        处理后的步骤序列
    """
    n = len(steps)

    # 产生action chunk
    for i, step in enumerate(steps):
        indexs = [
            min(n - 1, i + (idx + 1) * action_stride - 1)
            for idx in range(action_chunk)
        ]
        step["actions"] = [steps[j]["actions"] for j in indexs]

    # 产生未来状态chunk
    for i, step in enumerate(steps):
        indexs = [
            min(n - 1, i + (idx + 1) * action_stride)
            for idx in range(action_chunk)
        ]
        step["future_states"] = [steps[j]["states"] for j in indexs]

    #准备观测
    for i in range(n - 1, -1, -1):
        step = steps[i]
        if use_history:
            # 如果使用历史，准备历史观测
            # NOTE: 准备image历史观测，sample_history所需的history_obs_num和stride均固定为4，表示3帧历史+1帧当前观测，如需修改，可在函数内调整
            # 根据当前实验效果，仅采用image history效果最好，因此目前只对image进行采样，后续如需增加对robot state的采样，可与image_window_fids采用相同的方式生成frame的index。
            # 如增加了robot state采样，优先将image的采样index赋值给robot state，保持两者对齐。但两者分别送入不同的encoder网络，也可以各自独立采样，建议robot state的采样频率大于等于image 的采样频率。
            image_indices = sample_history(time_step=i)
            step["images"] = [steps[j]["images"] for j in image_indices]
            step["images_idx"] = image_indices
            step["states"] = [steps[i]["states"]]
        else:
            # 不使用历史，直接包装当前帧
            step["images"] = [steps[i]["images"]]
            step["states"] = [steps[i]["states"]]
    return steps[0 : -action_chunk * action_stride]

def state_to_action(states: Dict) -> Dict:
    """
    将状态转换为动作，确保没有None值

    Args:
        states: 状态字典

    Returns:
        动作字典，包含各个机械臂和夹具的动作
    """
    return dict(
        arm1_joints_action_abs=states.get(
            "arm1_joints_state", [0.0] * 7
        ),  # 提供默认值
        arm2_joints_action_abs=states.get(
            "arm2_joints_state", [0.0] * 7
        ),  # 提供默认值
        arm1_gripper_action_abs=(
            states.get("arm1_gripper_state", [0.0])[:1]
            if states.get("arm1_gripper_state") is not None
            else [0.0]
        ),
        arm2_gripper_action_abs=(
            states.get("arm2_gripper_state", [0.0])[:1]
            if states.get("arm2_gripper_state") is not None
            else [0.0]
        ),
        arm1_sucker_action_abs=(
            states.get("arm1_sucker_state", [0.0])[:1]
            if states.get("arm1_sucker_state") is not None
            else [0.0]
        ),
        arm2_sucker_action_abs=(
            states.get("arm2_sucker_state", [0.0])[:1]
            if states.get("arm2_sucker_state") is not None
            else [0.0]
        ),
    )

def infer_action(
    steps: Sequence[Mapping],
    from_state: bool = False,
) -> Sequence[Mapping]:
    """
    从状态推断动作

    Args:
        steps: 步骤序列
        from_state: 是否从状态推断动作

    Returns:
        推断动作后的步骤序列
    """
    if from_state:
        steps = [
            {**steps[i], "actions": state_to_action(steps[i + 1]["states"])}
            for i in range(len(steps) - 1)
        ]

    return steps

def _build_map_data_pipe(
    *,
    episode_files: Sequence[str] = None,
    proprio_state_obs_window_size: int,
    use_history: bool,
    action_chunk_size: int,
    action_stride: int,
    load_image: bool,
    load_depth: bool,
) -> MapDataPipe:
    """
    构建基于全局帧索引的数据管道

    Args:
        episode_files: episode文件路径序列
        其他参数与GlobalFrameDataset构造函数相同

    Returns:
        基于全局帧索引的映射数据集
    """
    dataset = GlobalFrameDataset(
        episode_files=episode_files,
        proprio_state_obs_window_size=proprio_state_obs_window_size,
        use_history=use_history,
        action_chunk_size=action_chunk_size,
        action_stride=action_stride,
        load_image=load_image,
        load_depth=load_depth,
    )
    return dataset

class GlobalFrameDataset(MapDataPipe):
    """基于全局帧索引的Dataset，实现按需帧级取样与chunk构建。
    设计原则:
      - 索引与窗口逻辑集中
      - 图像按需加载（历史窗口+当前帧）+ LRU 缓存
      - 不修改 episode 原始 steps 结构，所有加工在返回样本时完成
    """

    def __init__(
        self,
        *,
        episode_files: Sequence[str],
        proprio_state_obs_window_size: int,
        use_history: bool,
        action_chunk_size: int,
        action_stride: int,
        load_image: bool,
        load_depth: bool,
    ):
        self.episode_files = list(episode_files)
        # config
        self.proprio_state_obs_window_size = proprio_state_obs_window_size
        self.use_history = use_history
        self.action_chunk_size = action_chunk_size
        self.action_stride = action_stride
        self.load_image = load_image
        self.load_depth = load_depth
        # 全局帧索引 manifest: shape: [N,2] -> episode_id, frame_id
        self.manifest = self._load_episodes_and_generate_global_index(
            self.episode_files
        )

    def _load_episodes_and_generate_global_index(self, json_files: Sequence[str]) -> np.ndarray:
        """
        内部方法：统一加载episode信息并生成全局索引

        Args:
            json_files: JSON文件路径序列

        Returns:
            全局帧索引数组，形状为[N, 2]，每行为[episode_id, frame_id]
        """
        # 初始化进度条
        progress_bar = tqdm(
            total=len(json_files),
            desc="Generating global index",
            unit="files",
        )
        # 单线程顺序处理所有文件
        all_num_steps = []
        for episode_id, json_file in enumerate(json_files):
            num_steps = self._get_num_steps(json_file)
            effective_num_steps = (
                num_steps - self.action_chunk_size * self.action_stride
            )  # 一个episode中有效帧数
            if effective_num_steps < 0:
                progress_bar.update(1)
                continue
            all_num_steps.append(
                (episode_id, effective_num_steps)
            )  # 减去尾部无效帧
            progress_bar.update(1)

        progress_bar.close()

        # 处理结果
        total_frames = sum(
            effective_num_steps for _, effective_num_steps in all_num_steps
        )

        # 构建manifest
        manifest = np.empty((total_frames, 2), dtype=np.int32)
        current_idx = 0

        for episode_id, num_steps in all_num_steps:
            end_idx = current_idx + num_steps
            manifest[current_idx:end_idx, 0] = np.int32(episode_id)
            manifest[current_idx:end_idx, 1] = np.arange(
                num_steps, dtype=np.int32
            )
            current_idx = end_idx
        return manifest

    def _get_num_steps(self, json_path: str) -> int:
        """
        使用ijson流式读取episode的num_steps信息

        Args:
            json_path: JSON文件路径

        Returns:
            步数，如果读取失败抛出异常
        """
        try:
            with open(json_path, "rb") as f:
                # 只解析metadata中的num_steps
                parser = ijson.parse(f)
                num_steps = 0
                for prefix, event, value in parser:
                    # 取出metadata.num_steps，并检查事件类型确保类型安全
                    if prefix == "metadata.num_steps" and event == "number":
                        num_steps = int(value)
                        break  # 找到num_steps后就退出，提高效率

                return num_steps  # 在try块内返回，确保操作成功后才返回

        except Exception as e:
            logging.error(f"读取num_steps失败: {json_path}, 错误: {str(e)}")
            raise e

    def __len__(self):
        return len(self.manifest)

    def _load_frames_images(
        self,
        camera_keys,
        frames_needed: List[int],
        json_path: Optional[str] = None,
    ) -> List[Dict]:
        """
        高效读取episode中的多个指定帧（使用OpenCV后备）

        Args:
            camera_keys: 摄像头键
            frames_needed: 需要加载的帧索引列表
            json_path: JSON文件路径

        Returns:
            按帧索引组织的图像数据列表
        """
        if json_path is not None:
            filepath_base = os.path.splitext(json_path)[0]
        else:
            raise AttributeError(
                "_load_frames_images 需要提供 json_path 才能定位视频文件"
            )

        if camera_keys:
            camera_keys = list(dict.fromkeys(camera_keys))

        result: List[Dict[str, Optional[np.ndarray]]] = [{} for _ in range(len(frames_needed))]
        for camera_key in camera_keys:
            video_file_path = f"{filepath_base}_{camera_key}.mp4"
            frames_list = load_specific_frames_opencv(video_file_path, frames_needed)
            for i in range(len(frames_needed)):
                result[i][camera_key] = frames_list[i]

        return result

    def _process_frame(self, episode_id: int, frame_id: int) -> Dict:
        """
        构建单帧样本，包含历史观测和未来动作

        Args:
            episode_id: episode索引
            frame_id: 帧索引

        Returns:
            包含states、images、actions、future_states的字典
        """
        episode_file = self.episode_files[episode_id]
        filepath_base = episode_file.rsplit(".", 1)[0]
        hdf5_path = filepath_base + ".hdf5"

        def decode_value(val: Any) -> Any:
            return val.decode("utf-8") if isinstance(val, bytes) else val

        def extract_group_by_indices(group, indices: List[int]) -> List[Dict]:
            """批量读取 group 中的数据"""

            if not indices:
                return []

            # 预取 key 列表并确定有效 key（非 is_null）
            keys = [k for k in group.keys() if not group[k].attrs.get("is_null", False)]

            # 一次读取所有 keys 所需的批量索引数据
            raw_data = {k: group[k][indices] for k in keys}

            # 转置组织结构：将 column-based 转为 row-based
            length = len(indices)
            return [{k: raw_data[k][i] for k in keys} for i in range(length)]

        def extract_camera_keys(metadata) -> List[str]:
            """提取摄像头键值"""

            patterns = []
            if self.load_image:
                patterns.append(r"camera[0-9]+_rgb_resolution")
            if self.load_depth:
                patterns.append(r"camera[0-9]+_depth_resolution")
            keys = []
            for pattern in patterns:
                for k in metadata.keys():
                    if re.match(pattern, k) and not metadata[k].attrs.get('is_null',False):
                        keys.append(k.rsplit("_", 1)[0])
            return keys

        result = {}

        with h5py.File(hdf5_path, "r") as f:
            metadata = f["metadata"]
            obs_group = f["steps"]["observation"]
            action_group = f["steps"]["action"]

            num_steps = metadata["num_steps"][()]

            # 产生 actions chunk
            actions_indices = [
                min(num_steps - 1, frame_id + (idx + 1) * self.action_stride - 1)
                for idx in range(self.action_chunk_size)
            ]
            result["actions"] = extract_group_by_indices(action_group, actions_indices)

            # 产生 future states
            future_state_indices = [min(i + 1, num_steps - 1) for i in actions_indices]
            result["future_states"] = extract_group_by_indices(obs_group, future_state_indices)

            # 产生当前 state（单帧读取）
            result["states"] = [
                {
                    k: obs_group[k][frame_id]
                    for k in obs_group.keys()
                    if not obs_group[k].attrs.get("is_null", False)
                }
            ]

            if self.use_history:
                # NOTE: 准备image历史观测，sample_history所需的history_obs_num和stride均固定为4，表示3帧历史+1帧当前观测，如需修改，可在函数内调整
                # 根据当前实验效果，仅采用image history效果最好，因此目前只对image进行采样，后续如需增加对robot state的采样，可与image_window_fids采用相同的方式生成frame的index。
                # 如增加了robot state采样，优先将image的采样index赋值给robot state，保持两者对齐。但两者分别送入不同的encoder网络，也可以各自独立采样，建议robot state的采样频率大于等于image 的采样频率。
                image_window_fids = sample_history(time_step=frame_id)
                result["images_idx"] = image_window_fids
            else:
                image_window_fids = [frame_id]

            # 加载图像帧并记录时间
            camera_keys = extract_camera_keys(metadata)
            result["images"] = self._load_frames_images(
                camera_keys,
                image_window_fids,
                episode_file,
            )

            # 补充其他元信息
            result["tasks"] = {
                "language_instructions": [
                    decode_value(x) for x in metadata["lang_instructions"][()].tolist()
                ],
                "task_name": decode_value(metadata["task_name"][()]),
            }

            meta_keys = [
                "dataset_name",
                "experiment_time",
                "scene",
                "environment",
                "robot_name",
                "robot_type",
            ]
            result["metadata"] = {
                "time_step": frame_id,
                **{
                    k: decode_value(metadata[k][()]) for k in meta_keys if k in metadata
                },
            }

        return result

    def __getitem__(self, idx: int) -> Dict:
        """
        获取指定索引的样本

        Args:
            idx: 样本索引

        Returns:
            包含states、images、actions、future_states的样本字典
        """
        # 检查manifest索引有效性
        episode_id, frame_id = self.manifest[idx]
        sample = self._process_frame(episode_id, frame_id)
        return sample

def _build_iter_data_pipe(
    *,
    episode_files: Sequence[str] = None,
    shuffle_episodes: bool = True,
    # 历史相关参数
    proprio_state_obs_window_size: int = 1,
    use_history: bool = False,
    # action相关参数
    action_chunk_size: int = 1,
    action_stride: int = 1,
    # image相关参数
    load_image: bool = True,
    load_depth: bool = False,
    # 其他参数
    downsample_rate: float = 0.2,
    shuffle_buffer_size: Optional[int] = None,
    repeat: bool = False,
) -> IterDataPipe:
    """
    原始的基于episode的数据管道构建

    Args:
        episode_files: episode文件路径序列
        shuffle_episodes: 是否打乱episode顺序
        其他参数与build_dataset相同

    Returns:
        基于episode索引的迭代数据管道
    """

    assert 0.0 < downsample_rate <= 1.0, (
        f"downsample_rate should be in range (0,1], but got {downsample_rate}"
    )
    assert (
        isinstance(proprio_state_obs_window_size, int)
        and proprio_state_obs_window_size >= 1
    ), "proprio_state_obs_window_size必须为大于等于1的整数"
    assert (
        isinstance(action_chunk_size, int) and action_chunk_size >= 1
    ), "action_chunk_size必须为大于等于1的整数"
    # buffer模式参数校验
    if shuffle_buffer_size is not None:
        assert (
            isinstance(shuffle_buffer_size, int) and shuffle_buffer_size >= 1
        ), "shuffle_buffer_size必须为大于等于1的整数"

    # 直接使用列表而不是生成器，避免pickle问题
    episode_files = list(episode_files)[:]
    if shuffle_episodes:
        random.shuffle(episode_files)

    def shuffled_file_iter(episode_files: List[str]):
        """
        为不同worker提供不同的episode顺序，避免多个worker返回相同的结果

        Args:
            episode_files: episode文件列表

        Yields:
            打乱后的episode文件路径
        """
        worker_info = torch.utils.data.get_worker_info()
        if worker_info and worker_info.id != 0:  # 不同的worker设置不同的seed
            random.seed(random.random() + worker_info.id)
            random.shuffle(episode_files)
        yield from episode_files

    episode_files = shuffled_file_iter(episode_files)

    if repeat:
        episode_files = itertools.cycle(episode_files)

    dataset = (
        IterableWrapper(episode_files, deepcopy=False)
        .map(
            lambda x: Episode.load(
                x, load_image=load_image, load_depth=load_depth
            )
        )
        .map(restructure)
        .map(lambda x: infer_action(x))  # x[0] 是 steps, x[1] 是 metadata
        .map(
            lambda x: chunk_act_obs(
                x,
                action_chunk=action_chunk_size,
                action_stride=action_stride,
                proprio_state_obs_window_size=proprio_state_obs_window_size,
                use_history=use_history,
            )
        )
        .unbatch()  # 将trajectory粒度的数据拆分成frame粒度, flatten List[List[dict]] to  List[dict]
        .filter(lambda x: random.random() <= downsample_rate)
    )

    if shuffle_buffer_size:
        dataset = dataset.shuffle(buffer_size=shuffle_buffer_size)

    return dataset


def build_dataset(
    *,
    episode_files: Sequence[str] = None,
    shuffle_episodes: bool = True,
    # 历史相关参数
    proprio_state_obs_window_size: int = 1,
    use_history: bool = False,
    # action相关参数
    action_chunk_size: int = 1,
    action_stride: int = 1,
    # image相关参数
    load_image: bool = True,  # 数据统计时不想加载图像，提升速度
    load_depth: bool = False,
    # 其他参数
    downsample_rate: float = 0.2,
    shuffle_buffer_size: Optional[int] = None,
    repeat: bool = False,
    # 一次性索引所有帧再全局洗牌（保留兼容）
    datapipe_mode: str = "map_datapipe",  # iter_datapipe | map_datapipe
) -> Union[IterDataPipe, MapDataPipe]:
    """
    构建数据集

    Args:
        episode_files: episode文件路径序列
        shuffle_episodes: 是否打乱episode顺序
        proprio_state_obs_window_size: 状态观测窗口大小
        use_history: 是否使用历史观测
        action_chunk_size: 动作块大小
        action_stride: 动作步长
        load_image: 是否加载图像
        load_depth: 是否加载深度
        downsample_rate: 下采样比率
        shuffle_buffer_size: 洗牌缓冲区大小
        repeat: 是否重复数据
        sampling_mode: 采样模式
            - "iter_datapipe": 返回IterDataPipe数据类型，原流式 + 可选 buffer shuffle
            - "map_datapipe": 返回MapDataPipe数据类型，全局帧索引 + 全局洗牌

    Returns:
        迭代(IterDataPipe)或映射(MapDataPipe)数据集
    """
    assert datapipe_mode in [
        "map_datapipe",
        "iter_datapipe",
    ], f"未知 sampling_mode={datapipe_mode}"

    # 参数校验
    assert 0.0 < downsample_rate <= 1.0, (
        f"downsample_rate should be in range (0,1], but got {downsample_rate}"
    )
    assert (
        isinstance(proprio_state_obs_window_size, int)
        and proprio_state_obs_window_size >= 1
    ), "proprio_state_obs_window_size必须为大于等于1的整数"
    assert (
        isinstance(action_chunk_size, int) and action_chunk_size >= 1
    ), "action_chunk_size必须为大于等于1的整数"

    # 分支1: 全局帧索引模式
    if datapipe_mode.startswith("map_datapipe"):
        return _build_map_data_pipe(
            episode_files=episode_files,
            proprio_state_obs_window_size=proprio_state_obs_window_size,
            use_history=use_history,
            action_chunk_size=action_chunk_size,
            action_stride=action_stride,
            load_image=load_image,
            load_depth=load_depth,
        )

    # 分支2: 旧的局部连续帧采样模式
    elif datapipe_mode.startswith("iter_datapipe"):
        return _build_iter_data_pipe(
            episode_files=episode_files,
            shuffle_episodes=shuffle_episodes,
            proprio_state_obs_window_size=proprio_state_obs_window_size,
            use_history=use_history,
            action_chunk_size=action_chunk_size,
            action_stride=action_stride,
            load_image=load_image,
            load_depth=load_depth,
            downsample_rate=downsample_rate,
            shuffle_buffer_size=shuffle_buffer_size,
            repeat=repeat,
        )
    else:
        raise ValueError(f"不支持的 sampling_mode: {datapipe_mode}")
