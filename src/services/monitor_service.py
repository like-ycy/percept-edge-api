# src/services/monitor_service.py
"""Monitor 服务 — 缓存管理、定时轮询、Metadata 映射"""

import asyncio

from datetime import datetime, timezone

from loguru import logger

from libs.contracts.schema.episode_dataclass import Metadata
from src.schemas.monitor import (
    ComponentStatus,
    CpuInfo,
    DiskInfo,
    MemoryInfo,
    PlatformInfo,
    RobotMetadataInfo,
    RobotStatus,
    SystemInfo,
)
from src.services.robot_command_service import RobotCommandService


class MonitorService:
    """Monitor 服务

    职责：
    - 启动时查询一次 ZeroMQ monitor，缓存系统和机器人信息
    - 每 300 秒定时轮询刷新缓存
    - 提供 get_system_info()、get_robot_status() 供 API 调用
    - 提供 build_metadata() 供采集服务构建 episode Metadata
    """

    def __init__(
        self,
        command_service: RobotCommandService,
        poll_interval: int = 300,
    ):
        self._command_service = command_service
        self._poll_interval = poll_interval

        # 缓存
        self._system_cache: dict = {}
        self._robot_cache: dict = {}
        self._last_update: datetime | None = None

        # 轮询任务
        self._poll_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """启动服务：首次查询 + 启动定时轮询"""
        await self._refresh()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(f"MonitorService 已启动，轮询间隔 {self._poll_interval}s")

    async def stop(self) -> None:
        """停止轮询"""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("MonitorService 已停止")

    async def refresh(self) -> bool:
        """手动刷新缓存（供 API 调用）"""
        return await self._refresh()

    def get_system_info(self) -> SystemInfo | None:
        """获取系统信息（从缓存）"""
        if not self._system_cache:
            return None
        return SystemInfo(
            cpu=CpuInfo(**self._system_cache["cpu"]),
            memory=MemoryInfo(**self._system_cache["memory"]),
            disk=DiskInfo(**self._system_cache["disk"]),
            platform=PlatformInfo(**self._system_cache["platform"]),
        )

    def get_robot_status(self) -> RobotStatus | None:
        """获取机器人状态（从缓存）"""
        if not self._robot_cache:
            return None

        metadata_raw = self._robot_cache.get("metadata", {})
        register_info = metadata_raw.get("robot_register_info")

        metadata = RobotMetadataInfo(
            robot_id=metadata_raw.get("robot_id", ""),
            profile=metadata_raw.get("profile", ""),
            runtime_kind=metadata_raw.get("runtime_kind", ""),
            robot_model=metadata_raw.get("robot_model", ""),
            robot_type=metadata_raw.get("robot_type", ""),
            robot_desc=self._extract_robot_description_lines(metadata_raw),
            robot_register_info=register_info if isinstance(register_info, dict) else None,
        )

        components = []
        for key, value in self._robot_cache.items():
            if key == "metadata" or not isinstance(value, dict):
                continue
            components.append(
                ComponentStatus(
                    component_id=key,
                    enabled=bool(value.get("enabled", False)),
                    state=str(value.get("state", "unknown")),
                    connect_state=str(value.get("connect_state", "unknown")),
                    role=value.get("role") if isinstance(value.get("role"), str) else None,
                    hz=value.get("hz", 0),
                    last_frame_at_ms=value.get("last_frame_at_ms"),
                    frame_count=value.get("frame_count"),
                    error_count=value.get("error_count"),
                    last_error=value.get("last_error"),
                    width=value.get("width"),
                    height=value.get("height"),
                    jpeg_quality=value.get("jpeg_quality"),
                    depth_scale=value.get("depth_scale"),
                    brand=value.get("brand"),
                    model=value.get("model"),
                    detail=value.get("detail"),
                    joint_data_dim=value.get("joint_data_dim"),
                    eef_data_dim=value.get("eef_data_dim"),
                    gripper_data_dim=value.get("gripper_data_dim"),
                    joint_speed_dim=value.get("joint_speed_dim"),
                    current_dim=value.get("current_dim"),
                    effort_dim=value.get("effort_dim"),
                )
            )

        return RobotStatus(metadata=metadata, components=components)

    def is_cache_ready(self) -> bool:
        """检查 monitor 缓存是否已就绪"""
        return bool(self._system_cache) and bool(self._robot_cache)

    def build_metadata(self) -> Metadata:
        """Build episode Metadata from the current monitor cache."""
        if not self._robot_cache:
            return Metadata(num_steps=0)

        metadata_raw = self._robot_cache.get("metadata", {})
        robot_description = "\n".join(self._extract_robot_description_lines(metadata_raw))

        return Metadata(
            sample_rate=self._extract_sample_rate(),
            num_steps=0,
            robot_name=metadata_raw.get("robot_model"),
            robot_type=metadata_raw.get("robot_type"),
            robot_description=robot_description or None,
        )

    @staticmethod
    def _extract_robot_description_lines(metadata_raw: dict) -> list[str]:
        for key in ("robot_description", "robot_desc"):
            raw_value = metadata_raw.get(key)
            if isinstance(raw_value, str):
                line = raw_value.strip()
                if line:
                    return [line]
                continue
            if isinstance(raw_value, list):
                lines = [
                    item.strip() for item in raw_value if isinstance(item, str) and item.strip()
                ]
                if lines:
                    return lines
        return []

    def _extract_sample_rate(self) -> int | None:
        for key, value in self._robot_cache.items():
            if key == "metadata" or not isinstance(value, dict):
                continue
            hz = value.get("hz")
            if isinstance(hz, bool):
                continue
            if isinstance(hz, int) and hz > 0:
                return hz
        return None

    @property
    def last_update(self) -> datetime | None:
        """最后一次成功更新的时间"""
        return self._last_update

    async def _poll_loop(self) -> None:
        """定时轮询 monitor"""
        while self._running:
            await asyncio.sleep(self._poll_interval)
            if not self._running:
                break
            await self._refresh()

    @staticmethod
    def _normalize_monitor_v2(data: dict) -> tuple[dict, dict]:
        if data.get("type") != "runtime_monitor" or data.get("version") != 2:
            raise ValueError("monitor payload must be runtime_monitor v2")
        system = data.get("system")
        robot = data.get("robot")
        components = data.get("components")
        if (
            not isinstance(system, dict)
            or not isinstance(robot, dict)
            or not isinstance(components, dict)
        ):
            raise ValueError("monitor payload missing system, robot, or components")

        raw_metadata = robot.get("metadata")
        metadata_raw: dict = raw_metadata if isinstance(raw_metadata, dict) else {}
        robot_cache: dict = {
            "metadata": {
                "robot_id": robot.get("robot_id", ""),
                "profile": robot.get("profile", ""),
                "runtime_kind": robot.get("runtime_kind", ""),
                **metadata_raw,
            }
        }
        robot_cache.update(components)
        return system, robot_cache

    async def _refresh(self) -> bool:
        """查询 monitor 并更新缓存"""
        try:
            data = await self._command_service.query_monitor()
            if not data:
                logger.warning("monitor 查询返回空数据")
                return False

            self._system_cache, self._robot_cache = self._normalize_monitor_v2(data)
            self._last_update = datetime.now(timezone.utc)

            logger.debug(
                "monitor 缓存已更新: {} 个组件",
                len(self._robot_cache) - 1,  # 减去 metadata
            )
            return True
        except Exception as e:
            logger.error(f"monitor 刷新失败: {e}")
            return False
