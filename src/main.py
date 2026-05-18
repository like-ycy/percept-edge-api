# src/main.py
"""FastAPI 应用入口"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from src.api import collection, debug, device, health, monitor, storage, tasks, upload
from src.config import get_selected_env_name, get_selected_robot_name, get_settings
from src.core.app_context import AppContext, AppRuntimeState, AppServices, get_app_context
from src.core.exception_handlers import (
    app_exception_handler,
    generic_exception_handler,
    validation_exception_handler,
    value_error_handler,
)
from src.core.exceptions import AppException
from src.core.logging import logger
from src.core.middleware import AuthMiddleware, LocalOnlyMiddleware
from src.core.sync_throttle import SyncThrottle
from src.core.task_manager import BackgroundTaskManager
from src.models.database import CollectionRecord, get_db_engine, init_database, now_shanghai
from src.schemas.collection import CollectionRecordStatusEnum
from src.schemas.response import ResponseSchema
from src.schemas.validation import ValidationResult, ValidationStatusEnum
from src.services.cloud_client import CloudClient
from src.services.collection_lock_service import CollectionLockService
from src.services.collection_service import CollectionService
from src.services.heartbeat_client import HeartbeatClient
from src.services.monitor_service import MonitorService
from src.services.process_monitor import ProcessMonitor
from src.services.task_sync_service import TaskSyncService
from src.services.upload_service import UploadService
from src.services.webrtc_service import WebRTCService
from src.services.zeromq_consumer import ZeroMQConsumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    settings = get_settings()
    runtime = AppRuntimeState()
    selected_robot = get_selected_robot_name() or "unknown"
    selected_env = get_selected_env_name() or "unknown"
    logger.info(
        f"应用启动，机器人: {selected_robot}，环境: {selected_env}，调试模式: {settings.server.debug}"
    )

    # 初始化资源
    db_session_factory = await init_database(settings.database)
    db_engine = get_db_engine(db_session_factory)
    lock_service = CollectionLockService(db_session_factory)
    await lock_service.ensure_row()
    logger.info("CollectionLockService initialized")
    task_manager = BackgroundTaskManager()
    cloud_client = CloudClient(settings)
    sync_throttle = SyncThrottle(interval_seconds=settings.task.interval)
    task_sync_service = TaskSyncService(
        session_factory=db_session_factory,
        cloud_client=cloud_client,
        throttle=sync_throttle,
        interval_seconds=settings.task.interval,
        detail_concurrency=settings.task.detail_concurrency,
        token_provider=lambda: runtime.authorization,
    )

    await _recover_collection_records(db_session_factory)

    # 初始化采集组件
    zeromq_consumer = ZeroMQConsumer(
        settings.zeromq.endpoint, rep_endpoint=settings.zeromq.rep_endpoint
    )
    await zeromq_consumer.start()

    # 初始化 Monitor 服务（缓存系统信息和机器人元数据）
    monitor_service = MonitorService(zeromq_consumer, poll_interval=settings.monitor.interval)
    await monitor_service.start()

    webrtc_service = WebRTCService(zeromq_consumer, settings.webrtc)
    collection_service = CollectionService(
        zeromq_consumer,
        settings,
        task_manager=task_manager,
        cloud_client=cloud_client,
        monitor_service=monitor_service,
        lock_service=lock_service,
    )

    # 初始化上传服务（应用级单例）
    upload_service = UploadService(settings, cloud_client, task_manager=task_manager)

    # 将上传服务注入到采集服务（用于停止采集后自动上传）
    collection_service.set_upload_service(upload_service, db_session_factory)

    # 启动心跳服务
    heartbeat = None
    if settings.heartbeat.enabled:
        heartbeat = HeartbeatClient(settings)
        heartbeat.update_device_status(False, "")

    # 进程监控
    process_monitor = None
    process_monitor_cfg = getattr(settings, "process_monitor", None)
    if process_monitor_cfg and getattr(process_monitor_cfg, "enabled", False):
        process_monitor = ProcessMonitor(
            interval=process_monitor_cfg.sample_interval_seconds,
            refresh_window=process_monitor_cfg.refresh_window_seconds,
            cmdline_max_len=process_monitor_cfg.cmdline_max_length,
        )

    app.state.context = AppContext(
        settings,
        AppServices(
            db_session_factory,
            db_engine,
            task_manager,
            cloud_client,
            sync_throttle,
            task_sync_service,
            zeromq_consumer,
            monitor_service,
            webrtc_service,
            collection_service,
            upload_service,
            lock_service,
            heartbeat,
            process_monitor,
        ),
        runtime,
    )

    context = get_app_context(app)
    await _check_zmq_startup_health(context)
    await _resume_pending_materializations(context)
    await _resume_pending_validations(context)
    await _sync_device_activation_on_startup(context)
    await context.services.task_sync_service.start()
    if context.services.heartbeat:
        await context.services.heartbeat.start()
    if context.services.process_monitor:
        await context.services.process_monitor.start()

    yield

    context = get_app_context(app)

    # 停止心跳服务
    if context.services.heartbeat:
        await context.services.heartbeat.stop()
    if context.services.process_monitor:
        await context.services.process_monitor.stop()

    # 清理资源
    await context.services.monitor_service.stop()
    await zeromq_consumer.stop()
    await context.services.task_sync_service.stop()
    await context.services.task_manager.shutdown()
    await context.services.cloud_client.close()
    await context.services.db_engine.dispose()


app = FastAPI(
    title="Percept Edge API",
    description="边缘数据采集平台 API 服务",
    version="0.1.0",
    lifespan=lifespan,
)


# 注册认证中间件（需要在路由注册前）
def _setup_auth_middleware():
    settings = get_settings()
    app.add_middleware(
        AuthMiddleware,  # type: ignore[arg-type]
        whitelist=settings.auth.whitelist_paths,
        enabled=settings.auth.enabled,
    )


_setup_auth_middleware()


def _setup_local_only_middleware():
    app.add_middleware(
        LocalOnlyMiddleware,  # type: ignore[arg-type]
        protected_paths=["/debug", "/api/upload/active"],
    )


_setup_local_only_middleware()


# 注册全局异常处理器
app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(ValueError, value_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, generic_exception_handler)


class AppInfo(BaseModel):
    """应用信息"""

    message: str
    version: str
    robot: str
    env: str


@app.get("/", response_model=ResponseSchema[AppInfo])
async def root():
    """根路径"""
    return ResponseSchema(
        data=AppInfo(
            message="Percept Edge API",
            version="0.1.0",
            robot=get_selected_robot_name() or "unknown",
            env=get_selected_env_name() or "unknown",
        )
    )


# 注册路由
app.include_router(health.router, prefix="/health", tags=["健康检查"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["任务"])
app.include_router(storage.router, prefix="/api/storage", tags=["存储"])
app.include_router(upload.router, prefix="/api/upload", tags=["上传"])
app.include_router(collection.router, prefix="/api/collection", tags=["采集"])
app.include_router(monitor.router, prefix="/api/monitor", tags=["监控"])
app.include_router(device.router, prefix="/api/device", tags=["设备"])
app.include_router(debug.router, prefix="/debug", tags=["调试"])

# 挂载静态文件
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


async def _recover_collection_records(db_session_factory) -> None:
    """恢复历史上异常中断的采集记录状态"""
    async with db_session_factory() as session:
        result = await session.execute(
            select(CollectionRecord).where(
                CollectionRecord.collection_status == CollectionRecordStatusEnum.COLLECTING.value
            )
        )
        interrupted_records = result.scalars().all()
        for record in interrupted_records:
            record.collection_status = CollectionRecordStatusEnum.ABORTED.value
            logger.warning(f"标记异常中断的采集记录: {record.id}, 路径: {record.output_dir}")
        if interrupted_records:
            await session.commit()
            logger.info(f"共标记 {len(interrupted_records)} 条异常中断的采集记录")


async def _resume_pending_materializations(context: AppContext) -> None:
    """恢复待补偿的 raw spool 整理任务"""
    async with context.services.db_session_factory() as session:
        result = await session.execute(
            select(CollectionRecord).where(
                CollectionRecord.collection_status.in_(
                    [
                        CollectionRecordStatusEnum.FINALIZING.value,
                        CollectionRecordStatusEnum.FINALIZE_FAILED.value,
                    ]
                )
            )
        )
        records = result.scalars().all()
        resumable_records = []
        for record in records:
            if not record.raw_capture_dir:
                logger.warning(f"待整理记录缺少 raw_capture_dir，跳过: record_id={record.id}")
                continue
            capture_dir = Path(record.raw_capture_dir)
            sealed_path = capture_dir / "SEALED"
            manifest_path = capture_dir / "manifest.json"
            if not capture_dir.exists() or not sealed_path.exists() or not manifest_path.exists():
                if record.collection_status == CollectionRecordStatusEnum.FINALIZING.value:
                    record.collection_status = CollectionRecordStatusEnum.FINALIZE_FAILED.value
                    record.materialize_error = (
                        "恢复待整理任务失败：raw spool 未 seal 或 manifest 缺失"
                    )
                logger.warning(f"待整理记录不可恢复，标记或保留失败状态: record_id={record.id}")
                continue
            resumable_records.append(record)

        if records:
            await session.commit()

    for record in resumable_records:
        scheduled = context.services.collection_service.schedule_materialization(
            record.id,
            validation_upload_after_success=False,
        )
        if scheduled:
            logger.info(f"已恢复待整理记录: record_id={record.id}")
        else:
            logger.warning(f"恢复待整理记录失败，跳过重复调度: record_id={record.id}")


async def _resume_pending_validations(context: AppContext) -> None:
    """恢复待补偿的完整性校验任务"""
    async with context.services.db_session_factory() as session:
        result = await session.execute(
            select(CollectionRecord).where(
                CollectionRecord.collection_status == CollectionRecordStatusEnum.VALIDATING.value
            )
        )
        records = result.scalars().all()
        resumable_records = []
        for record in records:
            if not record.output_dir or not Path(record.output_dir).exists():
                logger.warning(f"待校验记录缺少输出目录，标记校验失败: record_id={record.id}")
                validation_result = ValidationResult(
                    status=ValidationStatusEnum.FAILED,
                    directory=record.output_dir or "",
                    expected_steps=0,
                    expected_files=[],
                    found_files=[],
                    missing_files=[],
                    extra_files=[],
                    errors=[],
                    summary="恢复待校验任务失败：输出目录不存在",
                )
                record.collection_status = CollectionRecordStatusEnum.VALIDATION_FAILED.value
                record.validation_status = validation_result.status.value
                record.validation_summary = validation_result.summary
                record.validation_result = validation_result.model_dump_json()
                record.validated_at = now_shanghai()
                continue
            resumable_records.append(record)

        if records:
            await session.commit()

    for record in resumable_records:
        scheduled = context.services.collection_service.schedule_validation(
            record.id,
            upload_after_success=False,
        )
        if scheduled:
            logger.info(f"已恢复待校验记录: record_id={record.id}")
        else:
            logger.warning(f"恢复待校验记录失败，跳过重复调度: record_id={record.id}")


async def _sync_device_activation_on_startup(context: AppContext) -> None:
    """启动时根据 monitor 的 mac 同步设备激活状态"""
    system_info = context.services.monitor_service.get_system_info()
    if system_info is None:
        logger.warning("启动时 monitor 数据未就绪，跳过设备激活状态同步")
        return

    mac = (system_info.platform.mac_address or "").strip()
    if not mac:
        logger.warning("启动时 monitor 缺少 mac_address，跳过设备激活状态同步")
        return

    try:
        result = await context.services.cloud_client.get_activation_status(mac)
    except Exception as exc:
        logger.warning(f"启动时同步设备激活状态失败: {exc}")
        return

    context.runtime.update_device_status(result.state, result.uid)
    heartbeat = context.services.heartbeat
    if heartbeat:
        heartbeat.update_device_status(result.state, result.uid)

    if result.state:
        logger.info(f"启动时设备已激活，device_uid={result.uid}")
    else:
        logger.info("启动时设备未激活")


async def _check_zmq_startup_health(context: AppContext) -> None:
    """启动时检查 collection 和 monitor 数据源是否就绪"""
    collection_ready = await context.services.zeromq_consumer.wait_until_collection_ready(
        timeout=5.0
    )
    if not collection_ready:
        logger.error("启动检查失败: robotos_collection 在 5 秒内未收到数据")

    monitor_service = context.services.monitor_service
    if not monitor_service.is_cache_ready():
        logger.error("启动检查失败: robotos_monitor 未返回有效数据")
