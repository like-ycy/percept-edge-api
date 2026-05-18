# src/dependencies.py
"""全局依赖注入"""

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.core.app_context import AppRuntimeState, AppServices, get_app_context
from src.core.task_manager import BackgroundTaskManager
from src.core.sync_throttle import SyncThrottle
from src.repositories.task_repo import TaskRepository
from src.schemas.auth import UserInfo
from src.services.cloud_client import CloudClient
from src.services.collection_service import CollectionService
from src.services.storage_service import DatabaseStorageService
from src.services.task_service import TaskService
from src.services.monitor_service import MonitorService
from src.services.upload_service import UploadService
from src.services.task_sync_service import TaskSyncService


def get_settings(request: Request) -> Settings:
    """获取配置"""
    return get_app_context(request.app).settings


def get_app_runtime(request: Request) -> AppRuntimeState:
    """获取应用运行态"""
    return get_app_context(request.app).runtime


def get_app_services(request: Request) -> AppServices:
    """获取应用服务注册表"""
    return get_app_context(request.app).services


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话"""
    async with get_app_services(request).db_session_factory() as session:
        yield session


def get_db_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂"""
    return get_app_services(request).db_session_factory


def get_task_manager(request: Request) -> BackgroundTaskManager:
    """获取后台任务管理器"""
    return get_app_services(request).task_manager


def get_task_repo(
    session: AsyncSession = Depends(get_db),
) -> TaskRepository:
    """获取任务 Repository"""
    return TaskRepository(session)


# ========== 认证依赖注入 ==========


def get_current_user(request: Request) -> UserInfo:
    """获取当前认证用户（必须已认证）"""
    user: UserInfo | None = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未认证")
    return user


def get_optional_user(request: Request) -> UserInfo | None:
    """获取当前用户（可选，未认证返回 None）"""
    return getattr(request.state, "user", None)


# ========== 云端同步依赖注入 ==========


def get_cloud_client(request: Request) -> CloudClient:
    """获取云端客户端"""
    return get_app_services(request).cloud_client


def get_sync_throttle(request: Request) -> SyncThrottle:
    """获取同步节流器"""
    return get_app_services(request).sync_throttle


def get_task_service(
    db: AsyncSession = Depends(get_db),
    cloud_client: CloudClient = Depends(get_cloud_client),
    throttle: SyncThrottle = Depends(get_sync_throttle),
) -> TaskService:
    """获取任务服务"""
    repo = TaskRepository(db)
    return TaskService(repo=repo, cloud_client=cloud_client, throttle=throttle)


def get_task_sync_service(request: Request) -> TaskSyncService:
    """获取后台任务同步服务（维护用户同步上下文与周期刷新）"""
    return get_app_services(request).task_sync_service


# ========== 采集模块依赖注入 ==========


def get_zeromq_consumer(request: Request):
    """获取 ZeroMQ 消费者

    Args:
        request: FastAPI 请求对象

    Returns:
        ZeroMQ 消费者实例
    """
    return get_app_services(request).zeromq_consumer


def get_collection_service(request: Request) -> CollectionService:
    """获取采集服务（应用级单例）

    Args:
        request: FastAPI 请求对象

    Returns:
        采集服务实例
    """
    return get_app_services(request).collection_service


def get_webrtc_service(request: Request):
    """获取 WebRTC 服务

    Args:
        request: FastAPI 请求对象

    Returns:
        WebRTC 服务实例
    """
    return get_app_services(request).webrtc_service


# ========== 存储模块依赖注入 ==========


def get_database_storage_service(
    db: AsyncSession = Depends(get_db),
) -> DatabaseStorageService:
    """获取数据库存储服务实例

    Args:
        db: 数据库会话

    Returns:
        数据库存储服务实例
    """
    return DatabaseStorageService(db=db)


# ========== 上传模块依赖注入 ==========


def get_upload_service(request: Request) -> UploadService:
    """获取上传服务实例（应用级单例）

    Args:
        request: FastAPI 请求对象

    Returns:
        上传服务实例
    """
    return get_app_services(request).upload_service


# ========== 监控模块依赖注入 ==========


def get_monitor_service(request: Request) -> MonitorService:
    """获取 Monitor 服务实例（应用级单例）"""
    return get_app_services(request).monitor_service


def get_process_monitor(request: Request):
    """获取进程监控服务（可能为 None）"""
    return get_app_services(request).process_monitor
