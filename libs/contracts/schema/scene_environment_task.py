from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import ijson
import yaml

SCENE_ENV_TASK_FILE = Path(__file__).with_name("scene_environment_task.yaml")
TASK_INSTRUCTION_MAP_FILE = Path(__file__).with_name("task_instruction_map.yaml")


_scene_data: Optional[Dict[str, Any]] = None
_task_instruction_data: Optional[Dict[str, Any]] = None


def _get_scene_data() -> Dict[str, Any]:
    """
    加载并缓存场景环境任务数据。
    如果数据已加载，则直接从缓存返回。
    """
    global _scene_data
    if _scene_data is None:
        with open(SCENE_ENV_TASK_FILE, "r", encoding="utf-8") as file:
            _scene_data = yaml.safe_load(file) or {}
    return _scene_data


def _get_task_instruction_data() -> Dict[str, Any]:
    """
    加载并缓存任务指令映射数据。
    如果数据已加载，则直接从缓存返回。
    """
    global _task_instruction_data
    if _task_instruction_data is None:
        with open(TASK_INSTRUCTION_MAP_FILE, "r", encoding="utf-8") as file:
            _task_instruction_data = yaml.safe_load(file) or {}
    return _task_instruction_data


def _get_task_level_tuple_array() -> Set[Tuple[str, str, str]]:
    """
    将YAML文件根据 task 级别转换为 (scene, environment, task_name) 的集合。

    Returns:
        Set[Tuple[str, str, str]]: 每个元素是 (scene, environment, task_name) 三元组
    """
    yaml_data = _get_scene_data()
    task_set = set()

    for scene in yaml_data.get("scenes", []):
        scene_name = scene.get("name")
        if not scene_name:
            continue

        for env in scene.get("environments", []):
            env_name = env.get("name")
            if not env_name:
                continue

            for task in env.get("tasks", []):
                task_name = task.get("name")
                if not task_name:
                    continue

                task_set.add((scene_name, env_name, task_name))

    return task_set


def get_scenes(robot_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    根据机器人名称获取场景。

    - 如果 robot_name 为 None, 返回所有场景。
    - 如果提供了 robot_name, 则只返回包含支持该机器人的任务的场景。

    Args:
        robot_name (str, optional): 机器人名称。默认为 None。

    Returns:
        List[Dict[str, Any]]: 场景列表, 每个场景包含 'name', 'description', 'supported_robots'。
    """
    data = _get_scene_data()
    all_scenes = data.get("scenes", [])
    filtered_scenes = []

    for scene in all_scenes:
        # 预先计算该场景下所有支持的机器人
        all_robots_in_scene = set()
        for env in scene.get("environments", []):
            for task in env.get("tasks", []):
                all_robots_in_scene.update(task.get("supported_robots", []))

        # 应用过滤条件：
        # 1. 如果 robot_name 为 None，则不过滤，直接添加该场景。
        # 2. 如果 robot_name 不为 None，则检查该机器人是否在场景支持的机器人列表中。
        if robot_name is None or robot_name in all_robots_in_scene:
            filtered_scenes.append(
                {
                    "name": scene.get("name"),
                    "description": scene.get("description"),
                    "supported_robots": sorted(list(all_robots_in_scene)),
                }
            )

    return filtered_scenes


def get_environments(
    robot_name: Optional[str] = None, scene_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    根据场景名称和机器人名称获取环境。

    - 如果所有参数都为 None, 返回所有环境。
    - 如果提供了任何参数，则将它们作为 "与" 条件来过滤结果。

    Args:
        robot_name (str, optional): 机器人名称。默认为 None。
        scene_name (str, optional): 场景名称。默认为 None。

    Returns:
        List[Dict[str, Any]]: 环境列表, 每个环境包含 'name', 'description', 'supported_robots'。
    """
    data = _get_scene_data()
    all_scenes = data.get("scenes", [])
    filtered_environments = []

    for scene in all_scenes:
        # 应用场景名称过滤
        if scene_name is not None and scene.get("name") != scene_name:
            continue

        for env in scene.get("environments", []):
            # 预先计算该环境下所有支持的机器人
            all_robots_in_env = set()
            for task in env.get("tasks", []):
                all_robots_in_env.update(task.get("supported_robots", []))

            # 应用机器人名称过滤
            if robot_name is not None and robot_name not in all_robots_in_env:
                continue

            # 如果所有条件都满足，则将该环境添加到结果列表
            filtered_environments.append(
                {
                    "name": env.get("name"),
                    "description": env.get("description"),
                    "incompatible_envs": env.get("incompatible_envs"),
                    "supported_robots": sorted(list(all_robots_in_env)),
                }
            )

    return filtered_environments


def get_tasks(
    robot_name: Optional[str] = None,
    scene_name: Optional[str] = None,
    environment_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    根据场景、环境和机器人名称获取任务。

    - 如果所有参数都为 None, 返回所有任务。
    - 如果提供了任何参数，则将它们作为 "与" 条件来过滤结果。

    Args:
        robot_name (str, optional): 机器人名称。默认为 None。
        scene_name (str, optional): 场景名称。默认为 None。
        environment_name (str, optional): 环境名称。默认为 None。

    Returns:
        List[Dict[str, Any]]: 任务列表, 每个任务包含详细信息。
    """
    data = _get_scene_data()
    all_scenes = data.get("scenes", [])
    filtered_tasks = []

    for scene in all_scenes:
        # 应用场景名称过滤
        if scene_name is not None and scene.get("name") != scene_name:
            continue

        for env in scene.get("environments", []):
            # 应用环境名称过滤
            if environment_name is not None and env.get("name") != environment_name:
                continue

            for task in env.get("tasks", []):
                supported_robots = task.get("supported_robots", [])
                # 应用机器人名称过滤
                if robot_name is not None and robot_name not in supported_robots:
                    continue

                # 如果所有条件都满足，则将该任务添加到结果列表
                filtered_tasks.append(
                    {
                        "name": task.get("name"),
                        "supported_robots": supported_robots,
                        "scene_name": scene.get("name"),
                        "environment_name": env.get("name"),
                    }
                )

    return filtered_tasks


def get_instructions(
    robot_name: Optional[str] = None,
    scene_name: Optional[str] = None,
    environment_name: Optional[str] = None,
    task_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    根据机器人、场景、环境和任务名称获取指令。

    - 如果所有参数都为 None, 返回所有指令。
    - 如果提供了任何参数，则将它们作为 "与" 条件来过滤结果。
    - 返回的每个指令字典中都会包含一个 'supported_robots' 列表，
      其数据来源于 scene_environment_task.yaml 文件。

    Args:
        robot_name (Optional[str], optional): 机器人名称。默认为 None。
        scene_name (Optional[str], optional): 场景名称。默认为 None。
        environment_name (Optional[str], optional): 环境名称。默认为 None。
        task_name (Optional[str], optional): 任务名称。默认为 None。

    Returns:
        List[Dict[str, Any]]: 指令字典列表, 每个字典都包含 'supported_robots' 字段。
    """
    # 1. 加载指令数据和场景/任务数据
    instruction_data = _get_task_instruction_data()
    scene_data = _get_scene_data()

    all_instructions = instruction_data.get("tasks", [])

    # 2. 创建一个从 task_name 到其所有支持的机器人的映射
    task_to_robots_map: Dict[str, set] = {}
    for scene in scene_data.get("scenes", []):
        for environment in scene.get("environments", []):
            for task in environment.get("tasks", []):
                current_task_name = task.get("name")
                if not current_task_name:
                    continue

                supported_robots = set(task.get("supported_robots", []))
                if current_task_name not in task_to_robots_map:
                    task_to_robots_map[current_task_name] = set()

                task_to_robots_map[current_task_name].update(supported_robots)

    # 3. 过滤指令
    filtered_instructions = []
    for instruction in all_instructions:
        current_instruction_task_name = instruction.get("name")
        if not current_instruction_task_name:
            continue

        # 应用任务名称过滤
        if task_name is not None and current_instruction_task_name != task_name:
            continue

        # 应用场景过滤
        if scene_name is not None and scene_name not in instruction.get("scenes", []):
            continue

        # 应用环境过滤
        if environment_name is not None and environment_name not in instruction.get(
            "environments", []
        ):
            continue

        # 获取当前任务支持的机器人列表
        supported_robots_for_task = task_to_robots_map.get(
            current_instruction_task_name, set()
        )

        # 应用机器人过滤
        if robot_name is not None and robot_name not in supported_robots_for_task:
            continue

        # 复制一份以避免修改原始缓存数据
        result_instruction = instruction.copy()
        result_instruction["supported_robots"] = sorted(list(supported_robots_for_task))

        # 如果所有条件都满足，则添加到结果列表
        filtered_instructions.append(result_instruction)

    return filtered_instructions


def validate_json_metadata_against_yaml(json_bytes: bytes) -> bool:
    """
    验证 JSON 字节内容中的 metadata 字段是否在 YAML 配置中有效。

    Args:
        json_bytes (bytes): 原始 JSON 数据 (应为 UTF-8 编码)。

    Returns:
        bool: 如果验证通过，返回 True。

    Raises:
        ValueError: 如果 metadata 缺失、不完整或不在 YAML 配置中。
    """
    # 获取 yaml 中的 task 级别 tuple 数组
    yaml_task_array = _get_task_level_tuple_array()

    def _extract_metadata_from_stream(stream: bytes) -> Tuple[str, str, str]:
        """
        (辅助函数) 从 JSON 字节流中提取并验证 metadata 字段。
        """
        try:
            metadata_iter = ijson.items(stream, "metadata")
            metadata = next(metadata_iter)
        except StopIteration:
            raise ValueError("JSON 数据中缺少 'metadata' 字段")
        except Exception as e:
            raise ValueError(f"解析 JSON 中的 'metadata' 失败. 详情: {e}")

        required_keys = {"scene", "environment", "task_name"}
        if not required_keys.issubset(metadata.keys()):
            missing = required_keys - set(metadata.keys())
            raise ValueError(f"'metadata' 缺少必需字段: {', '.join(missing)}")

        return (
            metadata["scene"],
            metadata["environment"],
            metadata["task_name"],
        )

    current_metadata = _extract_metadata_from_stream(json_bytes)
    if current_metadata not in yaml_task_array:
        raise ValueError(f"元数据 {current_metadata} 不在有效的 YAML 配置列表中。")

    return True
