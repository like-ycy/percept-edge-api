"""应用上下文对象"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from fastapi import FastAPI

from src.config import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from src.core.sync_throttle import SyncThrottle
    from src.core.task_manager import BackgroundTaskManager
    from src.services.cloud_client import CloudClient
    from src.services.collection_lock_service import CollectionLockService
    from src.services.collection_service import CollectionService
    from src.services.heartbeat_client import HeartbeatClient
    from src.services.monitor_service import MonitorService
    from src.services.process_monitor import ProcessMonitor
    from src.services.robot_command_service import RobotCommandService
    from src.services.task_sync_service import TaskSyncService
    from src.services.upload_service import UploadService
    from src.services.webrtc_service import WebRTCService
    from src.services.zeromq_consumer import ZeroMQConsumer


@dataclass
class DeviceRuntimeState:
    """设备运行态"""

    is_activated: bool = False
    device_id: str = ""


@dataclass
class AppRuntimeState:
    """应用运行态"""

    device: DeviceRuntimeState = field(default_factory=DeviceRuntimeState)
    authorization: str = ""

    def update_device_status(self, is_activated: bool, device_id: Optional[str]) -> None:
        """更新设备运行态"""
        self.device.is_activated = is_activated
        self.device.device_id = (device_id or "").strip()

    def update_authorization(self, authorization: Optional[str]) -> None:
        """更新运行时授权头"""
        self.authorization = (authorization or "").strip()


@dataclass
class AppServices:
    """应用服务注册表"""

    db_session_factory: "async_sessionmaker[AsyncSession]"
    db_engine: "AsyncEngine"
    task_manager: "BackgroundTaskManager"
    cloud_client: "CloudClient"
    sync_throttle: "SyncThrottle"
    task_sync_service: "TaskSyncService"
    zeromq_consumer: "ZeroMQConsumer"
    monitor_service: "MonitorService"
    webrtc_service: "WebRTCService"
    collection_service: "CollectionService"
    upload_service: "UploadService"
    collection_lock_service: "CollectionLockService | None" = None
    robot_command_service: "RobotCommandService | None" = None
    heartbeat: "HeartbeatClient | None" = None
    process_monitor: "ProcessMonitor | None" = None

    def __init__(
        self,
        db_session_factory: "async_sessionmaker[AsyncSession]",
        db_engine: "AsyncEngine",
        task_manager: "BackgroundTaskManager",
        cloud_client: "CloudClient",
        sync_throttle: "SyncThrottle",
        task_sync_service: "TaskSyncService",
        zeromq_consumer: "ZeroMQConsumer",
        monitor_service: "MonitorService",
        webrtc_service: "WebRTCService",
        collection_service: "CollectionService",
        upload_service: "UploadService",
        collection_lock_service: "CollectionLockService | None" = None,
        robot_command_service: "RobotCommandService | None" = None,
        heartbeat: "HeartbeatClient | None" = None,
        process_monitor: "ProcessMonitor | None" = None,
    ) -> None:
        self.db_session_factory = db_session_factory
        self.db_engine = db_engine
        self.task_manager = task_manager
        self.cloud_client = cloud_client
        self.sync_throttle = sync_throttle
        self.task_sync_service = task_sync_service
        self.zeromq_consumer = zeromq_consumer
        self.monitor_service = monitor_service
        self.webrtc_service = webrtc_service
        self.collection_service = collection_service
        self.upload_service = upload_service
        self.collection_lock_service = collection_lock_service
        self.robot_command_service = robot_command_service
        self.heartbeat = heartbeat
        self.process_monitor = process_monitor


@dataclass
class AppContext:
    """应用上下文"""

    settings: Settings
    services: AppServices
    runtime: AppRuntimeState = field(default_factory=AppRuntimeState)

    def __init__(
        self,
        settings: Settings,
        services: AppServices,
        runtime: Optional[AppRuntimeState] = None,
    ) -> None:
        self.settings = settings
        self.services = services
        self.runtime = runtime or AppRuntimeState()


def get_app_context(app: FastAPI) -> AppContext:
    """从 FastAPI app 获取应用上下文"""
    context = getattr(app.state, "context", None)
    if not isinstance(context, AppContext):
        raise RuntimeError("AppContext 未初始化")
    return context
