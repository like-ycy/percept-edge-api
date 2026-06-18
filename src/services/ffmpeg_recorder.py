# src/services/ffmpeg_recorder.py
"""FFmpeg 录制器

封装视频写入和 Episode 数据写入逻辑，与 CollectionService 解耦。
"""

from __future__ import annotations

import json


from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from loguru import logger

from libs.contracts.schema.episode_dataclass import Episode, Metadata, Observation, Step
from src.schemas.collection import ZmqFrame
from src.services.ffmpeg_writer import FFmpegMJPEGWriter
from src.services.collection_output_naming import (
    build_filename_prefix,
    build_video_filename,
    require_valid_filename_prefix,
)


class FFmpegRecorder:
    """FFmpeg 录制器

    负责管理视频写入和 Episode JSON 写入的生命周期。
    CollectionService 只需调用 write_frame() 方法，无需关心底层实现。

    特性：
    - 动态创建 FFmpeg 写入器（按需为每个摄像头创建）
    - 按 contracts Episode 模型直出 JSON
    - 帧完整性检查（保证所有视频帧数一致）
    - 统一的资源清理接口
    """

    _FRAME_COUNT_SNAPSHOT_FILE = "writer_frame_counts.snapshot"
    _VR_POSE_LENGTH = 16

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
        self._episode_path: Path | None = None
        self._steps: list[Step] = []
        self._started = False
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
        if self._started:
            raise RuntimeError("录制器已启动，请先调用 stop() 或 cleanup_on_error()")

        self._started = True
        self._start_time = start_time
        self._step_count = 0
        self._steps = []
        self._expected_cameras = None
        self._writer_frame_counts = {}
        self._skipped_frame_count = 0
        self._skipped_missing_camera_counts = {}
        self._skipped_extra_camera_counts = {}
        self._skipped_no_rgb_count = 0

        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._prepared_metadata = self._build_metadata(start_time)
        self._filename_prefix = self._build_filename_prefix(self._prepared_metadata)
        self._episode_path = self._output_dir / f"{self._filename_prefix}.json"

        logger.debug(
            "录制器已启动: {}, video_only={}, episode={}",
            self._output_dir,
            self._video_only,
            self._episode_path.name,
        )

    def write_frame(self, frame: ZmqFrame) -> bool:
        if not self._started or self._start_time is None or self._episode_path is None:
            raise RuntimeError("录制器未启动，请先调用 start() 方法")

        if self._expected_cameras is None:
            self._expected_cameras = {
                c.component_id for c in frame.cameras if c.color_data or c.depth_data
            }
            logger.debug(f"首帧确定摄像头列表: {self._expected_cameras}")

        current_cameras = {c.component_id for c in frame.cameras if c.color_data or c.depth_data}
        if current_cameras != self._expected_cameras:
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

        if self._video_only:
            for camera in frame.cameras:
                if camera.color_data:
                    self._get_or_create_rgb_writer(camera.component_id).write(camera.color_data)
                    self._steps.append(
                        Step(observation=Observation(timestamp=int(frame.timestamp * 1000)))
                    )
                    self._step_count += 1
                    return True
            logger.warning(
                "video_only 模式收到无 RGB 数据的帧，跳过: timestamp={}", frame.timestamp
            )
            self._record_skipped_no_rgb_frame()
            return False

        for camera in frame.cameras:
            if camera.color_data:
                self._get_or_create_rgb_writer(camera.component_id).write(camera.color_data)
            if camera.depth_data:
                self._get_or_create_depth_writer(camera.component_id).write(camera.depth_data)

        obs = self._convert_to_observation(frame)
        self._steps.append(Step(observation=obs))
        self._step_count += 1
        return True

    def stop(self) -> None:
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

        if self._episode_path:
            metadata = self._update_metadata_num_steps(self._step_count)
            episode = Episode(metadata=metadata, steps=self._steps)
            self._episode_path.write_text(
                json.dumps(episode.model_dump(exclude_none=True), ensure_ascii=False),
                encoding="utf-8",
            )
        self._started = False

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

        self._started = False

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
        lang_instructions = self._resolve_lang_instructions(base_metadata)
        sample_rate = (base_metadata.sample_rate if base_metadata else None) or self._fps
        robot_name = (
            (base_metadata.robot_name if base_metadata else None) or self._robot_name or None
        )

        if base_metadata:
            return base_metadata.model_copy(
                update={
                    "experiment_time": experiment_time,
                    "operator": self._collector_name or base_metadata.operator,
                    "task_name": self._task_name or base_metadata.task_name,
                    "lang_instructions": lang_instructions,
                    "sample_rate": sample_rate,
                    "num_steps": 0,
                    "robot_name": robot_name,
                }
            )

        return Metadata(
            experiment_time=experiment_time,
            operator=self._collector_name or None,
            task_name=self._task_name,
            lang_instructions=lang_instructions,
            sample_rate=sample_rate,
            num_steps=0,
            robot_name=robot_name,
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
            return require_valid_filename_prefix(self._configured_filename_prefix)
        if self._start_time is None:
            raise RuntimeError("录制器未启动，无法创建 episode 文件名前缀")
        return self.build_default_filename_prefix(self._start_time)

    @staticmethod
    def build_default_filename_prefix(start_time: datetime) -> str:
        return build_filename_prefix(start_time)

    def _get_video_filename(self, camera_id: str, stream_type: str) -> str:
        if self._filename_prefix is None:
            raise RuntimeError("录制器未启动，无法创建视频文件名")
        return build_video_filename(self._filename_prefix, camera_id, stream_type)

    def _convert_to_observation(self, frame: ZmqFrame) -> Observation:
        obs = Observation(timestamp=int(frame.timestamp * 1000))

        for arm in frame.arms:
            cid = arm.component_id
            eef_state = arm.eef or None
            gripper_state = arm.gripper or None

            if cid == "arm_left":
                obs.arm_left_joints_state = arm.joint_pos or None
                obs.arm_left_current_state = arm.joint_cur or None
                obs.arm_left_velocity_state = arm.joint_vel or None
                obs.arm_left_eef_state = eef_state
                obs.gripper_left_state = gripper_state
            elif cid == "arm_right":
                obs.arm_right_joints_state = arm.joint_pos or None
                obs.arm_right_current_state = arm.joint_cur or None
                obs.arm_right_velocity_state = arm.joint_vel or None
                obs.arm_right_eef_state = eef_state
                obs.gripper_right_state = gripper_state
            elif cid == "master_arm_left":
                obs.master_arm_left_joints_state = arm.joint_pos or None
                obs.master_arm_left_current_state = arm.joint_cur or None
                obs.master_arm_left_velocity_state = arm.joint_vel or None
                obs.master_arm_left_eef_state = eef_state
                obs.master_gripper_left_state = gripper_state
            elif cid == "master_arm_right":
                obs.master_arm_right_joints_state = arm.joint_pos or None
                obs.master_arm_right_current_state = arm.joint_cur or None
                obs.master_arm_right_velocity_state = arm.joint_vel or None
                obs.master_arm_right_eef_state = eef_state
                obs.master_gripper_right_state = gripper_state
            elif cid == "torso":
                obs.torso_joints_state = arm.joint_pos or None
                obs.torso_current_state = arm.joint_cur or None
                obs.torso_velocity_state = arm.joint_vel or None
            elif cid == "head":
                obs.head_joints_state = arm.joint_pos or None
                obs.head_current_state = arm.joint_cur or None
                obs.head_velocity_state = arm.joint_vel or None
            elif cid == "hand_left":
                obs.hand_left_joints_state = arm.joint_pos or None
                obs.hand_left_force_state = arm.joint_force or None
                obs.hand_left_velocity_state = arm.joint_vel or None
                obs.hand_left_current_state = arm.joint_cur or None
            elif cid == "hand_right":
                obs.hand_right_joints_state = arm.joint_pos or None
                obs.hand_right_force_state = arm.joint_force or None
                obs.hand_right_velocity_state = arm.joint_vel or None
                obs.hand_right_current_state = arm.joint_cur or None

            if arm.translation is not None:
                obs.translation_state = arm.translation
            if arm.tool_io_data is not None:
                obs.adsorption_state = [float(arm.tool_io_data)]

        for extra in frame.extras:
            payload = extra.payload
            if extra.component_id == "agv":
                base_state = self._extract_base_state(payload)
                if base_state is not None:
                    obs.base_state = base_state
            elif extra.component_id == "lift":
                height_float = self._coerce_float(payload.get("height"))
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
            elif extra.component_id == "translation_rail":
                translation_state = self._extract_translation_rail_state(payload)
                if translation_state is not None:
                    obs.translation_state = translation_state
            elif extra.component_id == "tool_io":
                io_state = self._coerce_float(payload.get("io_state"))
                if io_state is not None:
                    obs.adsorption_state = [io_state]

        return obs

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

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
    def _extract_base_state(payload: dict[str, Any]) -> list[float] | None:
        pose_data = payload.get("pose_data")
        if not isinstance(pose_data, dict):
            return None

        result: list[float] = []
        for value in pose_data.values():
            value_float = FFmpegRecorder._coerce_float(value)
            if value_float is None:
                return None
            result.append(value_float)
        return result

    @staticmethod
    def _extract_translation_rail_state(payload: dict[str, Any]) -> list[float] | None:
        result: list[float] = []
        for key in ("height", "height_norm", "min_height", "max_height"):
            value = FFmpegRecorder._coerce_float(payload.get(key))
            if value is None:
                return None
            result.append(value)
        return result

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
