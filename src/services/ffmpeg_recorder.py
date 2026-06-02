# src/services/ffmpeg_recorder.py
"""FFmpeg 录制器

封装视频写入和 Episode 数据流式写入的逻辑，与 CollectionService 解耦。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import IO, Any, ClassVar, cast

from loguru import logger

from libs.contracts.schema.episode_dataclass import Metadata, Observation
from src.schemas.collection import ZmqFrame
from src.services.ffmpeg_writer import FFmpegMJPEGWriter


class FFmpegRecorder:
    """FFmpeg 录制器

    负责管理视频写入和 Episode 数据流式写入的生命周期。
    CollectionService 只需调用 write_frame() 方法，无需关心底层实现。

    特性：
    - 动态创建 FFmpeg 写入器（按需为每个摄像头创建）
    - Episode JSON 流式写入（避免内存溢出）
    - 帧完整性检查（保证所有视频帧数一致）
    - 统一的资源清理接口
    """

    _FRAME_COUNT_SNAPSHOT_FILE = "writer_frame_counts.snapshot"
    _VR_POSE_LENGTH = 16
    _W1_ROBOT_NAMES: ClassVar[set[str]] = {"w1", "robot-w1"}
    _W1_BASE_KEYS: ClassVar[tuple[str, ...]] = (
        "linear_x",
        "linear_y",
        "angular_z",
        "left_wheel_speed_rpm",
        "right_wheel_speed_rpm",
        "left_wheel_current_a",
        "right_wheel_current_a",
    )
    _OBSERVATION_ARM_FIELDS: ClassVar[dict[str, set[str]]] = {
        "slave_arm1": {"joint_pos", "eef", "gripper", "translation"},
        "slave_arm2": {"joint_pos", "eef", "gripper"},
        "master_arm1": {"joint_pos", "eef", "gripper"},
        "master_arm2": {"joint_pos", "eef", "gripper"},
        "torso": {"joint_pos"},
        "head": {"joint_pos"},
        "hand1": {"joint_pos", "joint_force"},
        "hand2": {"joint_pos", "joint_force"},
    }
    _AGV_OBSERVATION_POSE_KEYS: ClassVar[set[str]] = {
        "x",
        "y",
        "theta",
        "position",
        "yaw",
        *_W1_BASE_KEYS,
    }

    def __init__(
        self,
        output_dir: Path,
        fps: int = 30,
        robot_name: str = "",
        metadata: Metadata | None = None,
        collector_name: str = "",
        task_name: str | None = None,
        lang_instructions: list[str] | None = None,
        filename_prefix: str | None = None,
        video_only: bool = False,
    ):
        """初始化录制器

        Args:
            output_dir: 输出目录
            fps: 视频帧率，默认 30
            robot_name: 机器人名称
            metadata: 预填充的 Metadata（来自 MonitorService）
            collector_name: 采集人名称
            task_name: 任务名称（可选）
            lang_instructions: 任务语言指令（可选）
        """
        self._output_dir = output_dir
        self._fps = fps
        self._collector_name = collector_name
        self._task_name = task_name
        self._lang_instructions = lang_instructions
        self._configured_filename_prefix = filename_prefix
        self._video_only = video_only

        # FFmpeg 写入器字典（RGB 和 Depth 分开）
        self._rgb_writers: dict[str, FFmpegMJPEGWriter] = {}
        self._depth_writers: dict[str, FFmpegMJPEGWriter] = {}

        # Episode 流式写入
        self._episode_file: IO[str] | None = None
        self._episode_path: Path | None = None
        self._step_count: int = 0
        self._robot_name: str = robot_name
        self._metadata: Metadata | None = metadata
        self._prepared_metadata: Metadata | None = None
        self._start_time: datetime | None = None
        self._filename_prefix: str | None = None
        self._writer_frame_counts: dict[str, int] = {}
        self._skipped_frame_count = 0
        self._skipped_missing_camera_counts: dict[str, int] = {}
        self._skipped_extra_camera_counts: dict[str, int] = {}
        self._skipped_no_rgb_count = 0

        # 帧完整性检查：记录首帧的摄像头列表
        self._expected_cameras: set[str] | None = None

    def start(self, start_time: datetime) -> None:
        """开始录制

        初始化 Episode JSON 文件，准备接收帧数据。

        Args:
            start_time: 采集开始时间
        """
        if self._episode_file is not None or self._rgb_writers or self._depth_writers:
            raise RuntimeError("录制器已启动，请先调用 stop() 或 cleanup_on_error()")

        self._start_time = start_time
        self._step_count = 0
        self._expected_cameras = None  # 重置，等待首帧确定
        self._writer_frame_counts = {}
        self._skipped_frame_count = 0
        self._skipped_missing_camera_counts = {}
        self._skipped_extra_camera_counts = {}
        self._skipped_no_rgb_count = 0

        # 确保输出目录存在
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._prepared_metadata = self._build_metadata(start_time)
        self._filename_prefix = self._build_filename_prefix(self._prepared_metadata)

        if not self._video_only:
            # 初始化 Episode 流式写入文件
            self._episode_path = self._output_dir / f"{self._filename_prefix}.json"
            self._episode_file = open(self._episode_path, "w", encoding="utf-8")
            # 写入 JSON 开头，steps 数组开始
            self._episode_file.write('{"steps": [')

        logger.debug(
            "录制器已启动: {}, video_only={}, episode={}",
            self._output_dir,
            self._video_only,
            self._episode_path.name if self._episode_path else "",
        )

    def write_frame(self, frame: ZmqFrame) -> bool:
        """写入帧数据（零解码，直接写入 JPEG 字节）

        视频数据写入 FFmpeg，观测数据流式写入 episode.json。
        帧完整性检查：确保所有摄像头数据都存在，保持视频帧数一致。

        Args:
            frame: ZeroMQ 帧数据

        Returns:
            bool: 是否成功写入（帧完整时返回 True，不完整时跳过返回 False）

        Raises:
            RuntimeError: 如果在调用 start() 之前调用此方法
        """
        if self._start_time is None:
            raise RuntimeError("录制器未启动，请先调用 start() 方法")
        if not self._video_only and self._episode_file is None:
            raise RuntimeError("录制器未启动，请先调用 start() 方法")

        # 1. 首帧时记录预期的摄像头列表
        if self._expected_cameras is None:
            self._expected_cameras = {
                c.component_id for c in frame.cameras if c.color_data or c.depth_data
            }
            logger.debug(f"首帧确定摄像头列表: {self._expected_cameras}")

        # 2. 检查帧完整性：所有预期摄像头都有数据
        current_cameras = {c.component_id for c in frame.cameras if c.color_data or c.depth_data}
        if current_cameras != self._expected_cameras:
            # 帧不完整，跳过（保持所有输出一致性）
            missing = self._expected_cameras - current_cameras
            extra = current_cameras - self._expected_cameras
            self._record_skipped_frame(missing=missing, extra=extra)
            logger.warning(
                "帧数据不完整，跳过: timestamp={}, 缺少={}, 多余={}",
                frame.timestamp,
                missing,
                extra,
            )
            return False

        # 3. 写入摄像头数据（RGB 和 Depth）
        if self._video_only:
            for camera in frame.cameras:
                if camera.color_data:
                    rgb_writer = self._get_or_create_rgb_writer(camera.component_id)
                    rgb_writer.write(camera.color_data)
                    self._step_count += 1
                    return True
            logger.warning(
                "video_only 模式收到无 RGB 数据的帧，跳过: timestamp={}", frame.timestamp
            )
            self._record_skipped_no_rgb_frame()
            return False

        for camera in frame.cameras:
            # 写入 RGB 数据（直接写入 JPEG 字节，无需解码）
            if camera.color_data:
                rgb_writer = self._get_or_create_rgb_writer(camera.component_id)
                rgb_writer.write(camera.color_data)

            # 写入 Depth 数据（直接写入 JPEG 字节，无需解码）
            if camera.depth_data:
                depth_writer = self._get_or_create_depth_writer(camera.component_id)
                depth_writer.write(camera.depth_data)

        # 4. 流式写入 Observation 到 episode.json（嵌套格式）
        obs = self._convert_to_observation(frame)
        cur_observation = obs.model_dump(exclude_none=True)
        other_data = self._build_other_data(frame)
        if other_data:
            cur_observation["other_data"] = other_data

        # 构建嵌套结构：observation_ex.cur_observation
        nested_step = {"observation_ex": {"cur_observation": cur_observation}}

        step_json = json.dumps(nested_step, ensure_ascii=False)

        # 第一个 step 不加逗号前缀，后续 step 加逗号分隔
        episode_file = self._episode_file
        if episode_file is None:
            raise RuntimeError("录制器未启动，请先调用 start() 方法")
        if self._step_count > 0:
            episode_file.write(",")
        episode_file.write(step_json)
        self._step_count += 1

        # 定期刷新到磁盘（每 100 帧）
        if self._step_count % 100 == 0:
            episode_file.flush()

        return True

    def stop(self) -> None:
        """停止录制

        释放所有 FFmpeg 写入器，完成 Episode JSON 写入。
        """
        stop_started_at = perf_counter()
        writer_frame_counts = self._collect_writer_frame_counts()

        # 释放所有 FFmpeg 写入器
        release_started_at = perf_counter()
        for camera_id, writer in self._rgb_writers.items():
            writer.release()
        for camera_id, writer in self._depth_writers.items():
            writer.release()
        logger.info(
            "录制器释放 FFmpeg 写入器完成: output_dir={}, writer_count={}, duration_ms={:.1f}",
            self._output_dir,
            len(self._rgb_writers) + len(self._depth_writers),
            (perf_counter() - release_started_at) * 1000,
        )
        self._rgb_writers = {}
        self._depth_writers = {}
        self._writer_frame_counts = writer_frame_counts
        if not self._video_only:
            self._persist_writer_frame_counts()

        # 完成 Episode JSON 流式写入
        if self._episode_file:
            metadata = self._update_metadata_num_steps(self._step_count)

            # 构建嵌套结构：metadata_ex.standard_metadata
            metadata_dict = metadata.model_dump()
            nested_metadata = {"metadata_ex": {"standard_metadata": metadata_dict}}
            metadata_json = json.dumps(nested_metadata, ensure_ascii=False)

            self._episode_file.write(f"], {metadata_json[1:-1]}}}")
            self._episode_file.close()
            self._episode_file = None

        logger.info(
            "录制器停止完成: output_dir={}, steps={}, skipped_frames={}, duration_ms={:.1f}",
            self._output_dir,
            self._step_count,
            self._skipped_frame_count,
            (perf_counter() - stop_started_at) * 1000,
        )
        if self._skipped_frame_count > 0:
            logger.warning(
                "录制器跳帧汇总: output_dir={}, skipped_frames={}, missing_camera_counts={}, "
                "extra_camera_counts={}, no_rgb_frames={}",
                self._output_dir,
                self._skipped_frame_count,
                self._skipped_missing_camera_counts,
                self._skipped_extra_camera_counts,
                self._skipped_no_rgb_count,
            )

    def cleanup_on_error(self) -> None:
        """异常发生时清理资源

        释放所有资源但保留文件用于调试。
        """
        logger.warning("正在清理录制器资源...")

        # 释放所有 FFmpeg 写入器
        for writer in self._rgb_writers.values():
            try:
                writer.release()
            except Exception as e:
                logger.error(f"释放 RGB 写入器失败: {e}")
        for writer in self._depth_writers.values():
            try:
                writer.release()
            except Exception as e:
                logger.error(f"释放 Depth 写入器失败: {e}")
        self._rgb_writers = {}
        self._depth_writers = {}
        self._writer_frame_counts = {}

        # 关闭 Episode 文件（尝试写入部分数据）
        if self._episode_file:
            try:
                # 写入不完整的结尾，标记为错误状态（嵌套格式）
                error_metadata = {
                    "metadata_ex": {
                        "standard_metadata": {"error": True, "num_steps": self._step_count}
                    }
                }
                error_metadata_json = json.dumps(error_metadata, ensure_ascii=False)
                self._episode_file.write(f"], {error_metadata_json[1:-1]}}}")
                self._episode_file.close()
            except Exception as e:
                logger.error(f"关闭 Episode 文件失败: {e}")
            finally:
                self._episode_file = None
                self._episode_path = None
                self._prepared_metadata = None
                self._filename_prefix = None

        logger.warning(f"录制器资源清理完成，已录制 {self._step_count} 帧")

    def _collect_writer_frame_counts(self) -> dict[str, int]:
        """采集各输出视频的帧数快照"""
        frame_counts: dict[str, int] = {}
        for writer in self._rgb_writers.values():
            frame_counts[Path(writer.output_path).name] = writer.frame_count
        for writer in self._depth_writers.values():
            frame_counts[Path(writer.output_path).name] = writer.frame_count
        return frame_counts

    def _persist_writer_frame_counts(self) -> None:
        """将每路视频帧数快照持久化到输出目录"""
        if not self._writer_frame_counts and self._skipped_frame_count == 0:
            return

        snapshot_path = self._output_dir / self._FRAME_COUNT_SNAPSHOT_FILE
        snapshot_payload = {
            "step_count": self._step_count,
            "writer_frame_counts": self._writer_frame_counts,
            "skipped_frame_count": self._skipped_frame_count,
            "skipped_missing_camera_counts": self._skipped_missing_camera_counts,
            "skipped_extra_camera_counts": self._skipped_extra_camera_counts,
            "skipped_no_rgb_count": self._skipped_no_rgb_count,
        }
        snapshot_path.write_text(json.dumps(snapshot_payload, ensure_ascii=False), encoding="utf-8")

    def _record_skipped_frame(self, *, missing: set[str], extra: set[str]) -> None:
        self._skipped_frame_count += 1
        for camera_id in missing:
            self._skipped_missing_camera_counts[camera_id] = (
                self._skipped_missing_camera_counts.get(camera_id, 0) + 1
            )
        for camera_id in extra:
            self._skipped_extra_camera_counts[camera_id] = (
                self._skipped_extra_camera_counts.get(camera_id, 0) + 1
            )

    def _record_skipped_no_rgb_frame(self) -> None:
        self._skipped_frame_count += 1
        self._skipped_no_rgb_count += 1

    def _get_or_create_rgb_writer(self, camera_id: str) -> FFmpegMJPEGWriter:
        """动态创建 RGB FFmpeg 写入器"""
        if camera_id not in self._rgb_writers:
            filepath = self._output_dir / f"{self._get_video_filename(camera_id, 'rgb')}"
            self._rgb_writers[camera_id] = FFmpegMJPEGWriter(filepath, fps=self._fps)
        return self._rgb_writers[camera_id]

    def _get_or_create_depth_writer(self, camera_id: str) -> FFmpegMJPEGWriter:
        """动态创建 Depth FFmpeg 写入器"""
        if camera_id not in self._depth_writers:
            filepath = self._output_dir / f"{self._get_video_filename(camera_id, 'depth')}"
            self._depth_writers[camera_id] = FFmpegMJPEGWriter(filepath, fps=self._fps)
        return self._depth_writers[camera_id]

    def _build_metadata(self, start_time: datetime) -> Metadata:
        experiment_time = start_time.strftime("%Y%m%d%H%M%S")
        base_metadata = self._metadata
        dataset_name = (
            (base_metadata.dataset_name if base_metadata else None) or self._robot_name or "unknown"
        )
        robot_name = (
            (base_metadata.robot_name if base_metadata else None)
            or self._robot_name
            or dataset_name
        )
        scene = (base_metadata.scene if base_metadata else None) or robot_name
        environment = (base_metadata.environment if base_metadata else None) or robot_name
        lang_instructions = self._resolve_lang_instructions(base_metadata)

        if base_metadata:
            return base_metadata.model_copy(
                update={
                    "dataset_name": dataset_name,
                    "episode_id": 1,
                    "experiment_time": experiment_time,
                    "num_steps": 0,
                    "robot_name": robot_name,
                    "operator": self._collector_name,
                    "scene": scene,
                    "environment": environment,
                    "task_name": self._task_name,
                    "lang_instructions": lang_instructions,
                }
            )

        return Metadata(
            dataset_name=dataset_name,
            episode_id=1,
            experiment_time=experiment_time,
            num_steps=0,
            robot_name=robot_name,
            robot_type=None,
            operator=self._collector_name,
            scene=scene,
            environment=environment,
            task_name=self._task_name,
            lang_instructions=lang_instructions,
        )

    def _resolve_lang_instructions(self, base_metadata: Metadata | None) -> list[str]:
        if self._lang_instructions is not None:
            return self._lang_instructions
        if base_metadata and base_metadata.lang_instructions:
            return list(base_metadata.lang_instructions)
        return []

    def _update_metadata_num_steps(self, num_steps: int) -> Metadata:
        metadata = self._prepared_metadata
        if metadata is None:
            if self._start_time is None:
                raise RuntimeError("录制器未启动，无法更新 metadata")
            metadata = self._build_metadata(self._start_time)

        updated_metadata = metadata.model_copy(update={"num_steps": num_steps})
        self._prepared_metadata = updated_metadata
        return updated_metadata

    def _build_filename_prefix(self, metadata: Metadata) -> str:
        if self._configured_filename_prefix:
            return self._configured_filename_prefix
        if self._start_time is None:
            raise RuntimeError("录制器未启动，无法创建 episode 文件名前缀")
        return self.build_default_filename_prefix(self._start_time)

    @staticmethod
    def build_default_filename_prefix(start_time: datetime) -> str:
        experiment_time = start_time.strftime("%Y%m%d%H%M%S")
        return f"episode_{experiment_time}_{uuid.uuid4().hex}"

    def _get_video_filename(self, camera_id: str, stream_type: str) -> str:
        if self._filename_prefix is None:
            raise RuntimeError("录制器未启动，无法创建视频文件名")
        return (
            f"{self._filename_prefix}_{self._sanitize_filename_part(camera_id)}_{stream_type}.mp4"
        )

    @staticmethod
    def _sanitize_filename_part(value: str | None) -> str:
        text = (value or "unknown").strip()
        if not text:
            return "unknown"

        sanitized = re.sub(r'[\\/:*?"<>|\s]+', "-", text)
        sanitized = sanitized.strip("-._")
        return sanitized or "unknown"

    def _convert_to_observation(self, frame: ZmqFrame) -> Observation:
        """将 ZmqFrame 转换为 Episode 的 Observation"""
        obs = Observation(
            timestamp=int(frame.timestamp * 1000),
        )

        for arm in frame.arms:
            cid = arm.component_id

            eef_state = arm.eef or None

            gripper_state = arm.gripper

            if cid == "slave_arm1":
                obs.arm1_joints_state = arm.joint_pos or None
                obs.arm1_gripper_state = gripper_state
                if eef_state:
                    obs.arm1_eef_state = eef_state
                if arm.translation:
                    obs.translation_state = arm.translation
                if arm.tool_io_data is not None:
                    obs.adsorption_state = [float(arm.tool_io_data)]
            elif cid == "slave_arm2":
                obs.arm2_joints_state = arm.joint_pos or None
                obs.arm2_gripper_state = gripper_state
                if eef_state:
                    obs.arm2_eef_state = eef_state
            elif cid == "master_arm1":
                has_joint_data = len(arm.joint_pos) > 0
                obs.master_arm1_joints_state = arm.joint_pos if has_joint_data else None
                obs.master_arm1_gripper_state = gripper_state if has_joint_data else None
                if eef_state:
                    obs.master_arm1_eef_state = eef_state
            elif cid == "master_arm2":
                has_joint_data = len(arm.joint_pos) > 0
                obs.master_arm2_joints_state = arm.joint_pos if has_joint_data else None
                obs.master_arm2_gripper_state = gripper_state if has_joint_data else None
                if eef_state:
                    obs.master_arm2_eef_state = eef_state
            elif cid == "torso":
                obs.torso_joints_state = arm.joint_pos or None
            elif cid == "head":
                obs.head_joints_state = arm.joint_pos or None
            elif cid == "hand1":
                obs.hand1_joints_state = arm.joint_pos or None
                obs.hand1_force_state = arm.joint_force or None
            elif cid == "hand2":
                obs.hand2_joints_state = arm.joint_pos or None
                obs.hand2_force_state = arm.joint_force or None

        for extra in frame.extras:
            payload = extra.payload
            if extra.component_id == "agv":
                if self._is_w1_robot(self._robot_name):
                    base_state = self._extract_base_state_w1(payload)
                else:
                    base_state = self._extract_base_state(payload)
                if base_state is not None:
                    obs.base_state = base_state
            elif extra.component_id == "lift":
                height = payload.get("height")
                height_float = self._coerce_float(height)
                if height_float is not None:
                    obs.lift_state = [height_float]
            elif extra.component_id == "vr":
                head_pose = self._extract_float_list(
                    payload.get("head_pose"), expected_length=self._VR_POSE_LENGTH
                )
                left_hand_pose = self._extract_float_list(
                    payload.get("left_hand_pose"), expected_length=self._VR_POSE_LENGTH
                )
                right_hand_pose = self._extract_float_list(
                    payload.get("right_hand_pose"), expected_length=self._VR_POSE_LENGTH
                )
                if head_pose is not None:
                    obs.vr_head_eef_state = head_pose
                if left_hand_pose is not None:
                    obs.vr_hand_left_eef_state = left_hand_pose
                if right_hand_pose is not None:
                    obs.vr_hand_right_eef_state = right_hand_pose

        return obs

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @staticmethod
    def _is_w1_robot(robot_name: str) -> bool:
        return robot_name.strip().lower() in FFmpegRecorder._W1_ROBOT_NAMES

    @staticmethod
    def _extract_float_list(
        value: object, *, expected_length: int | None = None
    ) -> list[float] | None:
        if not isinstance(value, list):
            return None
        if expected_length is not None and len(value) != expected_length:
            return None

        result: list[float] = []
        for item in value:
            item_float = FFmpegRecorder._coerce_float(item)
            if item_float is None:
                return None
            result.append(item_float)
        return result

    @staticmethod
    def _extract_base_state_w1(payload: dict[str, Any]) -> list[float] | None:
        pose_data = payload.get("pose_data")
        if not isinstance(pose_data, dict):
            return None

        result: list[float] = []
        for key in FFmpegRecorder._W1_BASE_KEYS:
            value = FFmpegRecorder._coerce_float(pose_data.get(key))
            result.append(value if value is not None else 0.0)
        return result

    @staticmethod
    def _extract_base_state(payload: dict[str, Any]) -> list[float] | None:
        pose_data = payload.get("pose_data")
        if not isinstance(pose_data, dict):
            return None

        x = pose_data.get("x")
        y = pose_data.get("y")
        theta = pose_data.get("theta")
        x_float = FFmpegRecorder._coerce_float(x)
        y_float = FFmpegRecorder._coerce_float(y)
        theta_float = FFmpegRecorder._coerce_float(theta)
        if x_float is not None and y_float is not None and theta_float is not None:
            return [x_float, y_float, theta_float]

        position = pose_data.get("position")
        yaw = pose_data.get("yaw")
        yaw_float = FFmpegRecorder._coerce_float(yaw)
        if (
            isinstance(position, list)
            and len(position) >= 2
            and (position_x := FFmpegRecorder._coerce_float(position[0])) is not None
            and (position_y := FFmpegRecorder._coerce_float(position[1])) is not None
            and yaw_float is not None
        ):
            return [position_x, position_y, yaw_float]

        return None

    def _build_other_data(self, frame: ZmqFrame) -> dict[str, Any]:
        other_data: dict[str, Any] = {}

        for arm in frame.arms:
            component_payload: dict[str, Any] = {}
            observation_fields = self._OBSERVATION_ARM_FIELDS.get(arm.component_id, set())

            if arm.joint_pos and "joint_pos" not in observation_fields:
                component_payload["joint_pos"] = arm.joint_pos
            if arm.joint_vel:
                component_payload["joint_vel"] = arm.joint_vel
            if arm.joint_cur:
                component_payload["joint_cur"] = arm.joint_cur
            if arm.joint_eff:
                component_payload["joint_eff"] = arm.joint_eff
            if arm.joint_force and "joint_force" not in observation_fields:
                component_payload["joint_force"] = arm.joint_force

            eef_state = self._extract_raw_eef_state(arm)
            if eef_state is not None and "eef" not in observation_fields:
                component_payload["eef_state"] = eef_state
            if arm.gripper is not None and "gripper" not in observation_fields:
                component_payload["gripper_data"] = arm.gripper
            if arm.translation is not None and "translation" not in observation_fields:
                component_payload["translation_data"] = arm.translation

            if component_payload:
                other_data[arm.component_id] = component_payload

        for extra in frame.extras:
            payload = extra.payload
            if extra.component_id == "agv":
                agv_payload = {key: value for key, value in payload.items() if key != "pose_data"}
                pose_data = payload.get("pose_data")
                if isinstance(pose_data, dict):
                    typed_pose_data = cast(dict[str, Any], pose_data)
                    pose_other = {
                        key: value
                        for key, value in typed_pose_data.items()
                        if key not in self._AGV_OBSERVATION_POSE_KEYS
                    }
                    if pose_other:
                        agv_payload["pose_data"] = pose_other
                elif pose_data is not None:
                    agv_payload["pose_data"] = pose_data
                if agv_payload:
                    other_data[extra.component_id] = agv_payload
                continue

            elif extra.component_id == "lift":
                lift_payload = {key: value for key, value in payload.items() if key != "height"}
                if lift_payload:
                    other_data[extra.component_id] = lift_payload
                continue

            elif extra.component_id == "vr":
                vr_payload = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"head_pose", "left_hand_pose", "right_hand_pose"}
                }
                if vr_payload:
                    other_data[extra.component_id] = vr_payload
                continue

            other_data[extra.component_id] = payload

        return other_data

    @staticmethod
    def _extract_raw_eef_state(arm) -> list[float] | None:
        return arm.eef or None

    @property
    def step_count(self) -> int:
        """已录制的帧数"""
        return self._step_count

    @property
    def robot_name(self) -> str:
        """机器人名称"""
        return self._robot_name

    @property
    def start_time(self) -> datetime | None:
        """采集开始时间"""
        return self._start_time

    @property
    def output_dir(self) -> Path:
        """输出目录"""
        return self._output_dir

    @property
    def episode_path(self) -> Path | None:
        return self._episode_path
