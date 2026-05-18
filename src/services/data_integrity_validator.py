# src/services/data_integrity_validator.py
"""数据完整性校验服务"""

from __future__ import annotations

import asyncio
from pathlib import Path

import orjson
from loguru import logger

from src.schemas.monitor import ComponentStatus
from src.schemas.validation import (
    FileValidationError,
    ValidationResult,
    ValidationStatusEnum,
)
from src.services.monitor_service import MonitorService
from src.utils.ffprobe import ffprobe_frames


class ValidationContext:
    """快路径校验上下文"""

    def __init__(
        self,
        *,
        directory: Path,
        expected_steps: int,
        expected_files: list[str],
        found_files: list[str],
        missing_files: list[str],
        extra_files: list[str],
        errors: list[FileValidationError],
    ) -> None:
        self.directory = directory
        self.expected_steps = expected_steps
        self.expected_files = expected_files
        self.found_files = found_files
        self.missing_files = missing_files
        self.extra_files = extra_files
        self.errors = errors


class DataIntegrityValidator:
    """采集数据完整性校验器

    职责：
    - 从 MonitorService 动态生成期望的视频文件列表
    - 校验视频文件名完整性
    - 校验每个视频的帧数与 episode.json 中 steps 数一致
    """

    _FRAME_COUNT_SNAPSHOT_FILE = "writer_frame_counts.snapshot"

    def __init__(self, monitor_service: MonitorService | None, frame_drop_threshold: float = 0.10):
        self._monitor_service = monitor_service
        self._frame_drop_threshold = frame_drop_threshold
        self._episode_file_name: str | None = None
        self._frame_validation_semaphore = asyncio.Semaphore(2)

    def validate_fast(self, directory: Path) -> ValidationResult:
        """执行快速校验，不包含 ffprobe 帧数检查

        Args:
            directory: 采集数据目录路径

        Returns:
            ValidationResult: 校验结果
        """
        fast_result = self._build_fast_context(directory)
        if isinstance(fast_result, ValidationResult):
            return fast_result

        status = self._determine_status(
            fast_result.errors, fast_result.missing_files, fast_result.extra_files
        )
        summary = self._generate_summary(
            status,
            len(fast_result.found_files),
            fast_result.expected_steps,
            fast_result.missing_files,
            fast_result.extra_files,
            [],
        )

        return ValidationResult(
            status=status,
            directory=str(fast_result.directory),
            expected_steps=fast_result.expected_steps,
            expected_files=fast_result.expected_files,
            found_files=fast_result.found_files,
            missing_files=fast_result.missing_files,
            extra_files=fast_result.extra_files,
            errors=fast_result.errors,
            summary=summary,
        )

    async def validate_collection(self, directory: Path, mode: str = "full") -> ValidationResult:
        """校验采集目录的数据完整性

        Args:
            directory: 采集数据目录路径
            mode: fast | full，fast 跳过 ffprobe 深度校验

        Returns:
            ValidationResult: 校验结果
        """
        fast_result = self._build_fast_context(directory)
        if isinstance(fast_result, ValidationResult):
            return fast_result

        frame_errors: list[FileValidationError] = []
        if mode == "full" and self._should_run_deep_validation(fast_result):
            video_files = [
                fast_result.directory / file_name
                for file_name in fast_result.found_files
                if file_name in set(fast_result.expected_files)
            ]
            frame_errors = await self._validate_all_video_frames(
                video_files, fast_result.expected_steps
            )

        errors = [*fast_result.errors, *frame_errors]
        status = self._determine_status(errors, fast_result.missing_files, fast_result.extra_files)
        summary = self._generate_summary(
            status,
            len(fast_result.found_files),
            fast_result.expected_steps,
            fast_result.missing_files,
            fast_result.extra_files,
            frame_errors,
        )

        return ValidationResult(
            status=status,
            directory=str(fast_result.directory),
            expected_steps=fast_result.expected_steps,
            expected_files=fast_result.expected_files,
            found_files=fast_result.found_files,
            missing_files=fast_result.missing_files,
            extra_files=fast_result.extra_files,
            errors=errors,
            summary=summary,
        )

    async def validate_video_only_collection(
        self,
        directory: Path,
        *,
        expected_frames: int,
        mode: str = "full",
    ) -> ValidationResult:
        """校验虚拟机器人 video_only 采集目录。"""
        if not directory.exists() or not directory.is_dir():
            return self._build_failed_result(
                directory=directory,
                expected_steps=expected_frames,
                error=FileValidationError(
                    file_name="",
                    error_type="directory_error",
                    message=f"目录不存在或不是有效目录: {directory}",
                ),
                summary="目录不存在或不是有效目录",
            )

        found_files = sorted(file.name for file in directory.glob("*.mp4") if file.is_file())
        errors: list[FileValidationError] = []
        if len(found_files) != 1:
            errors.append(
                FileValidationError(
                    file_name="",
                    error_type="video_only_file_count",
                    message=f"video_only 模式期望 1 个 mp4，实际 {len(found_files)} 个",
                )
            )
        elif not found_files[0].endswith("_rgb.mp4"):
            errors.append(
                FileValidationError(
                    file_name=found_files[0],
                    error_type="video_only_file_name",
                    message=f"video_only 模式只允许 RGB mp4: {found_files[0]}",
                )
            )

        forbidden_files = sorted(
            file.name
            for file in directory.iterdir()
            if file.is_file()
            and (file.suffix in {".json", ".snapshot"} or file.name.endswith("_depth.mp4"))
        )
        for file_name in forbidden_files:
            errors.append(
                FileValidationError(
                    file_name=file_name,
                    error_type="video_only_forbidden_file",
                    message=f"video_only 模式不应生成文件: {file_name}",
                )
            )

        frame_errors: list[FileValidationError] = []
        if mode == "full" and len(found_files) == 1:
            frame_errors = await self._validate_all_video_frames(
                [directory / found_files[0]], expected_frames
            )

        errors.extend(frame_errors)
        missing_files = ["*.mp4"] if not found_files else []
        extra_files = (
            [*found_files[1:], *forbidden_files] if len(found_files) > 1 else forbidden_files
        )
        status = self._determine_status(errors, missing_files, extra_files)
        summary = self._generate_summary(
            status,
            len(found_files),
            expected_frames,
            missing_files,
            extra_files,
            frame_errors,
        )

        return ValidationResult(
            status=status,
            directory=str(directory),
            expected_steps=expected_frames,
            expected_files=[found_files[0]] if len(found_files) == 1 else ["*.mp4"],
            found_files=found_files,
            missing_files=missing_files,
            extra_files=extra_files,
            errors=errors,
            summary=summary,
        )

    def _build_fast_context(self, directory: Path) -> ValidationContext | ValidationResult:
        """构建快速校验上下文"""
        errors: list[FileValidationError] = []

        # 1. 检查目录是否存在
        if not directory.exists() or not directory.is_dir():
            return self._build_failed_result(
                directory=directory,
                expected_steps=0,
                error=FileValidationError(
                    file_name="",
                    error_type="directory_error",
                    message=f"目录不存在或不是有效目录: {directory}",
                ),
                summary="目录不存在或不是有效目录",
            )

        try:
            episode_path, expected_steps = self._read_episode_steps(directory)
            self._episode_file_name = episode_path.name
        except Exception as e:
            logger.error(f"读取 episode json 失败: {e}")
            return self._build_failed_result(
                directory=directory,
                expected_steps=0,
                error=FileValidationError(
                    file_name=self._episode_file_name or "",
                    error_type="json_error",
                    message=str(e),
                ),
                summary=f"读取 episode json 失败: {e}",
            )

        # 3. 从 MonitorService 获取期望文件列表
        if self._monitor_service is None:
            return self._build_failed_result(
                directory=directory,
                expected_steps=expected_steps,
                error=FileValidationError(
                    file_name="",
                    error_type="monitor_error",
                    message="MonitorService 未配置",
                ),
                summary="MonitorService 未配置",
            )

        robot_status = self._monitor_service.get_robot_status()
        if not robot_status:
            logger.warning("MonitorService 缓存未就绪，无法生成期望文件列表")
            return self._build_failed_result(
                directory=directory,
                expected_steps=expected_steps,
                error=FileValidationError(
                    file_name="",
                    error_type="monitor_error",
                    message="MonitorService 缓存未就绪",
                ),
                summary="MonitorService 缓存未就绪",
            )

        expected_files = self._generate_expected_files(robot_status.components)

        # 4. 获取实际文件列表
        found_files = sorted(
            [f.name for f in directory.glob("*.mp4") if self._extract_camera_id(f.name) is not None]
        )

        # 5. 校验文件名完整性
        expected_set = set(expected_files)
        found_set = set(found_files)

        missing_files = sorted(expected_set - found_set)
        extra_files = sorted(found_set - expected_set)

        # 记录缺失文件错误
        for missing in missing_files:
            errors.append(
                FileValidationError(
                    file_name=missing,
                    error_type="missing",
                    message=f"文件缺失: {missing}",
                )
            )

        # 记录多余文件警告
        for extra in extra_files:
            errors.append(
                FileValidationError(
                    file_name=extra,
                    error_type="extra",
                    message=f"多余文件: {extra}",
                )
            )

        return ValidationContext(
            directory=directory,
            expected_steps=expected_steps,
            expected_files=expected_files,
            found_files=found_files,
            missing_files=missing_files,
            extra_files=extra_files,
            errors=errors,
        )

    def _build_failed_result(
        self,
        *,
        directory: Path,
        expected_steps: int,
        error: FileValidationError,
        summary: str,
    ) -> ValidationResult:
        """构建失败结果"""
        return ValidationResult(
            status=ValidationStatusEnum.FAILED,
            directory=str(directory),
            expected_steps=expected_steps,
            expected_files=[],
            found_files=[],
            missing_files=[],
            extra_files=[],
            errors=[error],
            summary=summary,
        )

    def _should_run_deep_validation(self, context: ValidationContext) -> bool:
        """是否需要执行深度帧数校验"""
        if context.missing_files:
            return False
        expected_file_set = set(context.expected_files)
        return any(file_name in expected_file_set for file_name in context.found_files)

    def _read_writer_frame_snapshot(self, directory: Path) -> tuple[int | None, dict[str, int]]:
        """读取录制阶段持久化的帧数快照"""
        snapshot_path = directory / self._FRAME_COUNT_SNAPSHOT_FILE
        if not snapshot_path.exists() or not snapshot_path.is_file():
            return None, {}

        try:
            payload = orjson.loads(snapshot_path.read_bytes())
        except orjson.JSONDecodeError as exc:
            logger.warning(f"读取 writer 帧数快照失败: {snapshot_path}, error={exc}")
            return None, {}

        raw_counts = payload.get("writer_frame_counts")
        if not isinstance(raw_counts, dict):
            return None, {}

        frame_counts: dict[str, int] = {}
        for file_name, frame_count in raw_counts.items():
            if isinstance(file_name, str) and isinstance(frame_count, int):
                frame_counts[file_name] = frame_count

        step_count = payload.get("step_count")
        return (step_count if isinstance(step_count, int) else None), frame_counts

    def _generate_expected_files(self, components: list[ComponentStatus]) -> list[str]:
        """从 monitor 数据动态生成期望文件名

        Args:
            components: 组件状态列表

        Returns:
            期望的文件名列表（已排序）
        """
        if not self._episode_file_name:
            raise ValueError("缺少 episode 文件名，无法生成期望视频文件名")

        episode_stem = Path(self._episode_file_name).stem
        expected = []
        for comp in components:
            # 只处理已连接的相机
            if comp.connect_status != "connected":
                continue

            camera_id = comp.component_id
            if not camera_id.startswith("camera"):
                continue

            # RGB 判断：jpeg_quality 或 width/height 存在
            if comp.jpeg_quality is not None or (comp.width and comp.height):
                expected.append(f"{episode_stem}_{camera_id}_rgb.mp4")

            # Depth 判断：depth_scale 存在
            if comp.depth_scale is not None:
                expected.append(f"{episode_stem}_{camera_id}_depth.mp4")

        return sorted(expected)

    def _read_episode_steps(self, directory: Path) -> tuple[Path, int]:
        json_files = sorted(directory.glob("*.json"))
        if not json_files:
            raise ValueError(f"目录中不存在 episode json: {directory}")
        if len(json_files) > 1:
            raise ValueError(f"目录中存在多个 json 文件，无法确定 episode 文件: {directory}")

        episode_path = json_files[0]

        try:
            content = episode_path.read_bytes()
            data = orjson.loads(content)
            steps = data.get("steps")

            if steps is None:
                raise ValueError(f"{episode_path.name} 缺少 'steps' 字段: {directory}")

            return episode_path, len(steps)
        except orjson.JSONDecodeError as e:
            raise ValueError(f"{episode_path.name} 格式错误: {e}") from e

    def _extract_camera_id(self, file_name: str) -> str | None:
        parts = Path(file_name).stem.split("_")
        if len(parts) < 3:
            return None
        if parts[-1] not in {"rgb", "depth"}:
            return None
        camera_id = parts[-2]
        return camera_id if camera_id.startswith("camera") else None

    async def _validate_all_video_frames(
        self, video_files: list[Path], expected_frames: int
    ) -> list[FileValidationError]:
        """并发校验所有视频的帧数

        Args:
            video_files: 视频文件路径列表
            expected_frames: 期望的帧数

        Returns:
            错误列表
        """
        snapshot_step_count, snapshot_counts = self._read_writer_frame_snapshot(
            video_files[0].parent if video_files else Path()
        )
        tasks = [
            self._validate_video_frames(
                video_path,
                expected_frames,
                snapshot_frame_count=snapshot_counts.get(video_path.name),
                snapshot_step_count=snapshot_step_count,
            )
            for video_path in video_files
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"校验视频帧数时发生异常: {result}")
                continue
            if result is not None:
                errors.append(result)

        return errors

    async def _validate_video_frames(
        self,
        video_path: Path,
        expected_frames: int,
        snapshot_frame_count: int | None = None,
        snapshot_step_count: int | None = None,
    ) -> FileValidationError | None:
        """校验单个视频的帧数

        Args:
            video_path: 视频文件路径
            expected_frames: 期望的帧数

        Returns:
            错误信息，无错误返回 None
        """
        if snapshot_frame_count is not None and (
            snapshot_step_count is None or snapshot_step_count == expected_frames
        ):
            actual_frames = snapshot_frame_count
        else:
            # 限流执行 ffprobe，避免 stop 后后台校验打满磁盘 IO
            async with self._frame_validation_semaphore:
                actual_frames = await asyncio.to_thread(ffprobe_frames, video_path)

        if actual_frames is None:
            return FileValidationError(
                file_name=video_path.name,
                error_type="ffprobe_error",
                message=f"无法获取 {video_path.name} 的帧数",
            )

        if expected_frames <= 0:
            drop_rate = 0.0
        else:
            missing = max(expected_frames - actual_frames, 0)
            drop_rate = missing / expected_frames

        if drop_rate > self._frame_drop_threshold:
            return FileValidationError(
                file_name=video_path.name,
                error_type="frame_drop_exceeded",
                expected=expected_frames,
                actual=actual_frames,
                message=(
                    f"{video_path.name} 丢帧率 {drop_rate:.1%} 超过阈值 "
                    f"{self._frame_drop_threshold:.0%}（期望 {expected_frames}, 实际 {actual_frames}）"
                ),
            )

        return None

    def _determine_status(
        self,
        errors: list[FileValidationError],
        missing_files: list[str],
        extra_files: list[str],
    ) -> ValidationStatusEnum:
        """根据错误情况确定校验状态

        Args:
            errors: 错误列表
            missing_files: 缺失文件列表
            extra_files: 多余文件列表

        Returns:
            校验状态
        """
        if not errors:
            return ValidationStatusEnum.SUCCESS

        # 有缺失文件或帧数不匹配，视为失败
        has_critical_error = any(
            e.error_type
            in [
                "missing",
                "frame_drop_exceeded",
                "ffprobe_error",
                "video_only_file_count",
                "video_only_file_name",
                "video_only_forbidden_file",
            ]
            for e in errors
        )

        if has_critical_error:
            return ValidationStatusEnum.FAILED

        # 仅有多余文件，视为部分通过
        if extra_files and not missing_files:
            return ValidationStatusEnum.PARTIAL

        return ValidationStatusEnum.FAILED

    def _generate_summary(
        self,
        status: ValidationStatusEnum,
        file_count: int,
        expected_steps: int,
        missing_files: list[str],
        extra_files: list[str],
        frame_errors: list[FileValidationError],
    ) -> str:
        """生成校验结果摘要

        Args:
            status: 校验状态
            file_count: 实际文件数
            expected_steps: 期望帧数
            missing_files: 缺失文件列表
            extra_files: 多余文件列表
            frame_errors: 帧数错误列表

        Returns:
            摘要文本
        """
        if status == ValidationStatusEnum.SUCCESS:
            return f"数据完整性校验通过：{file_count} 个文件，{expected_steps} 帧"

        parts = ["数据完整性校验失败："]

        if missing_files:
            parts.append(f"{len(missing_files)} 个文件缺失")

        if extra_files:
            parts.append(f"{len(extra_files)} 个多余文件")

        if frame_errors:
            parts.append(f"{len(frame_errors)} 个文件丢帧超阈值或帧数获取失败")

        return "，".join(parts)
