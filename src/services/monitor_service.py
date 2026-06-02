# src/services/monitor_service.py
"""Monitor 服务 — 缓存管理、定时轮询、Metadata 映射"""

import asyncio
import re
from datetime import datetime, timezone

from loguru import logger

from libs.contracts.schema.episode_dataclass import CameraInfo, Metadata
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

# 组件 ID 到 Metadata 字段的映射
_ARM_FIELD_MAP = {
    "slave_arm1": "robot_arm1",
    "slave_arm2": "robot_arm2",
    "master_arm1": "robot_master_arm1",
    "master_arm2": "robot_master_arm2",
}


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
            robot_model=metadata_raw.get("robot_model", ""),
            robot_type=metadata_raw.get("robot_type", ""),
            robot_desc=metadata_raw.get("robot_desc", []),
            robot_register_info=register_info if isinstance(register_info, dict) else None,
        )

        components = []
        for key, value in self._robot_cache.items():
            if key == "metadata" or not isinstance(value, dict):
                continue
            components.append(
                ComponentStatus(
                    component_id=key,
                    connect_status=value.get("connect_status", "unknown"),
                    hz=value.get("hz", 0),
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
        """从 monitor 缓存构建预填充的 Metadata

        返回包含所有可从 monitor 获取的字段的 Metadata 对象。
        采集时再补充 num_steps、experiment_time 等运行时数据。
        """
        if not self._robot_cache:
            return Metadata(
                dataset_name=None,
                episode_id=None,
                num_steps=0,
            )

        metadata_raw = self._robot_cache.get("metadata", {})
        kwargs: dict = {
            "dataset_name": None,
            "episode_id": None,
            "num_steps": 0,
            "robot_name": metadata_raw.get("robot_model"),
            "robot_type": metadata_raw.get("robot_type"),
            "robot_description": "\n".join(metadata_raw.get("robot_desc", [])) or None,
        }

        # 遍历组件，填充相机和机械臂字段
        sample_rate_set = False
        for key, value in self._robot_cache.items():
            if key == "metadata" or not isinstance(value, dict):
                continue

            # 相机组件（以 camera 开头）
            camera_match = re.match(r"^camera(\d+)$", key)
            if camera_match:
                cam_num = camera_match.group(1)
                width = value.get("width")
                height = value.get("height")

                # sample_rate: 取第一个相机的 hz
                if not sample_rate_set and value.get("hz"):
                    kwargs["sample_rate"] = value["hz"]
                    sample_rate_set = True

                # RGB 分辨率
                if width and height:
                    kwargs[f"camera{cam_num}_rgb_resolution"] = [height, width]

                # 深度分辨率和 scale（仅有 depth_scale 的相机）
                if value.get("depth_scale") is not None:
                    if width and height:
                        kwargs[f"camera{cam_num}_depth_resolution"] = [height, width]
                    kwargs[f"camera{cam_num}_depth_scale"] = value["depth_scale"]

                # 相机型号信息
                if value.get("brand") and value.get("model"):
                    kwargs[f"camera{cam_num}_model_info"] = CameraInfo(
                        brand=value["brand"],
                        model=value["model"],
                        detail=value.get("detail"),
                    )
                continue

            # 机械臂组件
            if key in _ARM_FIELD_MAP:
                prefix = _ARM_FIELD_MAP[key]
                if value.get("joint_data_dim") is not None:
                    kwargs[f"{prefix}_joints_state_dim"] = value["joint_data_dim"]
                if value.get("eef_data_dim") is not None:
                    kwargs[f"{prefix}_eef_state_dim"] = value["eef_data_dim"]
                if value.get("gripper_data_dim") is not None:
                    kwargs[f"{prefix}_gripper_state_dim"] = value["gripper_data_dim"]

        return Metadata(**kwargs)

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

    async def _refresh(self) -> bool:
        """查询 monitor 并更新缓存"""
        try:
            data = await self._command_service.query_monitor()
            if not data:
                logger.warning("monitor 查询返回空数据")
                return False

            self._system_cache = data.get("system", {})
            self._robot_cache = data.get("robot", {})
            self._last_update = datetime.now(timezone.utc)

            logger.debug(
                "monitor 缓存已更新: {} 个组件",
                len(self._robot_cache) - 1,  # 减去 metadata
            )
            return True
        except Exception as e:
            logger.error(f"monitor 刷新失败: {e}")
            return False
