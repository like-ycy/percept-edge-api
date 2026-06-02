import itertools
import random
from typing import Mapping, Sequence, Union
import torch
from torch.utils.data.datapipes.datapipe import IterDataPipe
from torch.utils.data.datapipes.iter import *
from data.schema.episode_dataclass import *


def restructure(episode: Episode):
    """Extract a list of steps from `Episode` obj.
    Each step in the output list is a dict with keys including "actions","states","images","tasks","metadata".
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

def sample_history(i: int,
                       window_size: int,
                       stride: int,
                       mode: str,
                       n: int) -> List[int]:
    """History sampling (返回的索引长度恒等于 window_size，并在函数内完成 pad).

    args:
        i: 当前 step 的索引 (int)
        window_size: 采样窗口长度 (int, >=1). 等于1表示不增加历史，只取当前步。
        stride: 步长 (int, >=1)
        mode: "uniform" | "random" | "time_aware" | "time_aware_v2" | "multi_scale"
        n: 总步数 (int, >=1)
    """
    # ---- 基本检查 ----
    assert isinstance(i, numbers.Integral) and i >= 0, \
    f"i 必须是正整数，但实际是 {i} (类型: {type(i)})"
    assert isinstance(window_size, int) and window_size >= 1, "window_size 必须是 >=1 的整数"
    assert isinstance(stride, int) and stride >= 1, "stride 必须是 >=1 的整数"
    assert isinstance(n, int) and n >= 1, "n 必须是 >=1 的整数"
    assert mode in {"uniform", "random", "time_aware", "time_aware_v2","multi_scale"}, "未知采样模式"

    # ---- 特殊约定：window_size==1 表示不加历史，直接取当前步 ----
    if window_size == 1:
        return [min(i, n - 1)]

    # 统一限制 i 不越界
    i = min(i, n - 1)

    if mode == "uniform":
        indices = [
            max(0, i - idx * stride)
            for idx in range(window_size)
        ][::-1]  # 升序：旧 -> 新

    elif mode == "random":
        candidates = list(range(0, i + 1))
        k = min(window_size, len(candidates))
        indices = sorted(random.sample(candidates, k))

    elif mode == "time_aware":
        candidates = list(range(0, i + 1))
        # 越近权重越大：权重 ~ (stride)**-(i-idx)，但 stride=1 时会全1，仍可用
        weights = np.array([stride ** -(i - idx) for idx in candidates], dtype=float)
        denom = weights.sum()
        if denom <= 0:
            weights = np.ones_like(weights) / len(weights)
        else:
            weights = weights / denom
        k = min(window_size, len(candidates))
        chosen = np.random.choice(candidates, k, replace=False, p=weights)
        indices = sorted(chosen.tolist())
    elif mode == "time_aware_v2":
        #与time_aware相比，采样到远处历史的概率更大
        candidates = np.arange(0, i + 1)
        alpha = 1.2  # 1.0~1.3 比较平滑；越大越偏近端
        weights = (i - candidates + 1).astype(float) ** (-alpha)
        weights = weights / weights.sum()
        k = min(window_size, len(candidates))
        chosen = np.random.choice(candidates, k, replace=False, p=weights)
        indices = sorted(chosen.tolist())
    elif mode == "multi_scale":
        # 两种尺度合并，可能长度不足，最后 pad
        s = set()
        half = window_size // 2
        # 细尺度：步长=1
        s.update([max(0, i - idx) for idx in range(half)])
        # 粗尺度：步长=2*stride
        s.update([max(0, i - idx * stride * 2) for idx in range(window_size - half)])
        indices = sorted(s)
    else:
        raise ValueError(f"Unknown sampling mode: {mode}")

    while len(indices) < window_size:
        last = indices[-1] if indices else i
        indices.append(last)

    # 兜底：强制长度 == window_size（若多了，裁掉左侧，保留最近的）
    if len(indices) > window_size:
        indices = indices[-window_size:]
    # 强制包含当前步
    if indices[-1] != i:
        indices[-1] = i

    return indices

def chunk_act_obs(
    steps: Sequence[Mapping],
    action_chunk: int = 1,
    action_stride: int = 1,
    proprio_state_obs_window_size: int = 1,
    proprio_state_obs_stride: int = 1,
    image_obs_stride: int = 1,
    image_obs_window_size: int = 1,
    obs_sampling_mode: str = "time_aware", # "uniform", "random", "time_aware", "time_aware_v2","multi_scale",
    align_indices_of_proprio_states_and_images_for_history_obs: bool = True,      # 新增：默认保持原行为，用 image 的采样对齐 states
) -> Sequence[Mapping]:
    """Chunks actions and observations.

    The terms "states," "images," and "tasks" are given a new history axis, resulting in a shape of [observation_window_size].
    The term "actions" is expanded with two new axes, giving it a shape of [observation_window_size, action_chunk].
    Additionally, "future_states" is introduced at each step, with a shape of [observation_window_size, action_chunk]. This information is intended to be used as actions.

    The 'images' observation can have a different stride and window size than other state observations.
    """
    n = len(steps)

    # 产生 action chunk
    for i, step in enumerate(steps):
        indexs = [
            min(n - 1, i + (idx + 1) * action_stride - 1) for idx in range(action_chunk)
        ]
        step["actions"] = [steps[j]["actions"] for j in indexs]

    # 产生未来状态chunk
    for i, step in enumerate(steps):
        indexs = [
            min(n - 1, i + (idx + 1) * action_stride) for idx in range(action_chunk)
        ]
        step["future_states"] = [steps[j]["states"] for j in indexs]

    # 准备历史观测
    for i in range(n - 1, -1, -1):
        step = steps[i]
        # 图像历史索引（定长，已在 sample_history 内部 pad）
        image_indices = sample_history(i, image_obs_window_size, image_obs_stride, obs_sampling_mode, n)
        step["images"] = [steps[j]["images"] for j in image_indices]
        step["images_idx"] = image_indices  # <<< 关键：保存索引

        if align_indices_of_proprio_states_and_images_for_history_obs and (proprio_state_obs_window_size > 1):
            # 用 image 的索引去采样 states（完全对齐）,同时考虑不加proprio history情况
            state_indices = image_indices
        else:
            # 独立采样 states（使用 proprio 的窗口与步长）
            state_indices = sample_history(i, proprio_state_obs_window_size, proprio_state_obs_stride,
                                               obs_sampling_mode, n)
        step["states"] = [steps[j]["states"] for j in state_indices]
        step["states_idx"] = state_indices   # <<< 关键：保存索引

    # 去除后边不完整的数据 TODO: 改成使用mask的方式避免不完整数据的预测
    return steps[ 0 : -action_chunk*action_stride]


def state_to_action(states: dict) -> map:
    return dict(
        arm1_joints_action_abs=states["arm1_joints_state"],
        arm2_joints_action_abs=states["arm2_joints_state"],
        arm1_gripper_action_abs=states["arm1_gripper_state"][:1],
        arm2_gripper_action_abs=states["arm2_gripper_state"][:1],
        arm1_sucker_action_abs=(
            states["arm1_sucker_state"][:1] if states["arm1_sucker_state"] else None
        ),
        arm2_sucker_action_abs=(
            states["arm2_sucker_state"][:1] if states["arm2_sucker_state"] else None
        ),
    )


def infer_action(
    steps: Sequence[Mapping],
    from_state: bool = True,
) -> Sequence[Mapping]:


    if from_state:
        steps = [
            {**steps[i], "actions": state_to_action(steps[i + 1]["states"])}
            for i in range(len(steps) - 1)
        ]

    return steps


def build_dataset(
    *,
    episode_files: Sequence[str] = None,
    shuffle_episodes: bool = True,
    # observation相关参数
    proprio_state_obs_window_size: int = 1,
    proprio_state_obs_stride: int = 1,
    image_obs_window_size: int = 1,
    image_obs_stride: int = 1,
    obs_sampling_mode: str = "time_aware", # "uniform", "random", "time_aware","time_aware_v2", "multi_scale"
    align_indices_of_proprio_states_and_images_for_history_obs: bool = True,  # 新增：默认保持原行为，用 image 的采样对齐 states
    # action相关参数
    action_chunk_size: int = 1,
    action_stride: int = 1,
    # image相关参数
    load_image: bool = True, # 数据统计时不想加载图像，提升速度
    load_depth: bool = False,
    # 其他参数
    downsample_rate: float = 0.2,
    shuffle_buffer_size: int = None,
    repeat: bool = False,
) -> IterDataPipe:
    """Build a dataset as `IterDataPipe`

    args:
        episode_files: json文件列表
        shuffle_episodes: 是否对episode_files进行shuffle，trajectory粒度的shuffle操作.
        proprio_state_obs_window_size: 取多少个时间步的observation，包含当前step
        proprio_state_obs_stride:  每多少step采样一个observation加入observation window中，相当于对observation降频
        image_obs_window_size: 取多少个时间步的 image observation，包含当前step
        image_obs_stride: 每多少step采样一个image observation加入observation window中，如果为None，则使用observation_stride的值
        obs_sampling_mode: 图像观测和状态观测的历史采样方式，可以取uniform, random, time_aware, time_aware_v2,multi_scale
        align_indices_of_proprio_states_and_images_for_history_obs: 是否用 image 的采样索引去采样 states（完全对齐）。默认 True，保持原行为。
        action_chunk_size: 取多少个时间步的action,包含当前step，用于模型预测未来一段时间步的action.
        action_stride:  每多少step采样一个action加入action chunk中，相当于对action降频
        downsample_rate: 每个step被采样到的概率，范围(0,1]
        shuffle_buffer_size: 用于frame粒度样本shuffle操作的buffer大小, 默认为None，表示不做frame粒度样本shuffle。
        repeat: 数据耗尽之后重新迭代
    """

    assert (
        downsample_rate > 0.0 and downsample_rate <= 1.0
    ), f"downsample_rate should be in range (0,1], but got {downsample_rate}"

    assert isinstance(proprio_state_obs_window_size, int) and proprio_state_obs_window_size >= 1

    assert isinstance(action_chunk_size, int) and action_chunk_size >= 1

    assert isinstance(proprio_state_obs_stride, int) and proprio_state_obs_stride > 0
    assert isinstance(image_obs_stride, int) and image_obs_stride > 0


    if shuffle_buffer_size is not None:
        assert isinstance(shuffle_buffer_size, int) and shuffle_buffer_size >= 1

    episode_files = list(episode_files)[:]  # 复制一下
    if shuffle_episodes:
        random.shuffle(episode_files)

    def shuffled_file_iter(episode_files):
        """主要是为了在不同的worker中提供不同的顺序，避免多个worker返回相同的结果"""
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
        .map(lambda x: Episode.load(x, load_image=load_image, load_depth=load_depth))
        .map(restructure)
        .map(lambda x: infer_action(x))
        .map(
            lambda x: chunk_act_obs(
                x,
                action_chunk=action_chunk_size,
                action_stride=action_stride,
                proprio_state_obs_window_size=proprio_state_obs_window_size,
                proprio_state_obs_stride=proprio_state_obs_stride,
                image_obs_window_size=image_obs_window_size,
                image_obs_stride=image_obs_stride,
                obs_sampling_mode=obs_sampling_mode,
                align_indices_of_proprio_states_and_images_for_history_obs=align_indices_of_proprio_states_and_images_for_history_obs,
            )
        )
        .unbatch()  # 将trajectory粒度的数据拆分成frame粒度, flatten List[List[dict]] to  List[dict]
        .filter(lambda x: random.random() <= downsample_rate)
    )

    if shuffle_buffer_size:
        dataset = dataset.shuffle(buffer_size=shuffle_buffer_size)

    return dataset
