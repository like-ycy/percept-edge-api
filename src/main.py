# src/main.py
"""FastAPI 应用入口"""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from starlette.types import ExceptionHandler

from src.api import (
    collection,
    debug,
    desktop_control,
    device,
    health,
    monitor,
    robot_control,
    storage,
    tasks,
    upload,
)
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
from src.core.path_validator import PathValidationError, validate_safe_path
from src.core.sync_throttle import SyncThrottle
from src.core.task_manager import BackgroundTaskManager
from src.models.database import CollectionRecord, get_db_engine, init_database, now_shanghai
from src.schemas.collection import CollectionRecordStatusEnum
from src.schemas.response import ResponseSchema
from src.schemas.status import CloudNotifyStatus
from src.schemas.validation import ValidationResult, ValidationStatusEnum
from src.schemas.upload import UploadStatus
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

    storage_base = Path(settings.storage.base_path).resolve()
    await _recover_collection_records(db_session_factory, storage_base)

    # 初始化采集组件
    zeromq_consumer = ZeroMQConsumer(
        settings.zeromq.endpoint,
        enable_runtime_watchdog=settings.zeromq.enable_runtime_watchdog,
        stale_threshold_seconds=settings.zeromq.stale_threshold_seconds,
        watchdog_interval_seconds=settings.zeromq.watchdog_interval_seconds,
        startup_grace_seconds=settings.zeromq.startup_grace_seconds,
    )
    await zeromq_consumer.start()

    robot_command_service = RobotCommandService(settings.zeromq.command_endpoint)

    # 初始化 Monitor 服务（缓存系统信息和机器人元数据）
    monitor_service = MonitorService(
        robot_command_service,
        poll_interval=settings.monitor.interval,
    )
    await monitor_service.start()

    webrtc_service = WebRTCService(zeromq_consumer)
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
        settings=settings,
        services=AppServices(
            db_session_factory=db_session_factory,
            db_engine=db_engine,
            task_manager=task_manager,
            cloud_client=cloud_client,
            sync_throttle=sync_throttle,
            task_sync_service=task_sync_service,
            zeromq_consumer=zeromq_consumer,
            monitor_service=monitor_service,
            webrtc_service=webrtc_service,
            collection_service=collection_service,
            upload_service=upload_service,
            collection_lock_service=lock_service,
            robot_command_service=robot_command_service,
            heartbeat=heartbeat,
            process_monitor=process_monitor,
        ),
        runtime=runtime,
    )

    context = get_app_context(app)
    await _check_zmq_startup_health(context)
    await _resume_pending_materializations(context)
    await _resume_pending_validations(context)
    await _recover_interrupted_uploads(context)
    await _scan_pending_cloud_notify(context)
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
        protected_paths=["/debug", "/api/upload/active", "/api/desktop"],
    )


_setup_local_only_middleware()


# 注册全局异常处理器
app.add_exception_handler(AppException, cast(ExceptionHandler, app_exception_handler))
app.add_exception_handler(
    RequestValidationError, cast(ExceptionHandler, validation_exception_handler)
)
app.add_exception_handler(ValueError, cast(ExceptionHandler, value_error_handler))
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
app.include_router(robot_control.router, prefix="/api/robot", tags=["机器人控制"])
app.include_router(desktop_control.router, prefix="/api/desktop", tags=["桌面端控制"])
app.include_router(debug.router, prefix="/debug", tags=["调试"])

# 挂载静态文件
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


async def _recover_collection_records(db_session_factory, storage_base: Path) -> None:
    """恢复历史上异常中断的采集记录状态"""
    async with db_session_factory() as session:
        result = await session.execute(
            select(CollectionRecord).where(
                CollectionRecord.collection_status == CollectionRecordStatusEnum.COLLECTING.value
            )
        )
        interrupted_records = result.scalars().all()
        for record in interrupted_records:
            if _resume_collecting_record_from_sealed_spool(record, storage_base):
                logger.warning(
                    f"恢复异常中断但已 seal 的采集记录: {record.id}, 路径: {record.output_dir}"
                )
                continue
            record.collection_status = CollectionRecordStatusEnum.ABORTED.value
            if record.output_dir:
                capture_dir = _resolve_capture_dir_from_output_dir(
                    output_dir=record.output_dir,
                    storage_base=storage_base,
                )
                if capture_dir is not None and capture_dir.exists():
                    record.raw_capture_dir = str(capture_dir)
            logger.warning(f"标记异常中断的采集记录: {record.id}, 路径: {record.output_dir}")
        if interrupted_records:
            await session.commit()
            logger.info(f"共标记 {len(interrupted_records)} 条异常中断的采集记录")


def _resolve_capture_dir_from_output_dir(
    *, output_dir: Optional[str], storage_base: Path
) -> Optional[Path]:
    if not output_dir:
        return None
    try:
        validated_output_dir = validate_safe_path(
            output_dir,
            allowed_base=storage_base,
            allow_relative=False,
        )
    except PathValidationError as exc:
        logger.warning(
            "恢复采集记录失败，output_dir 非法: output_dir={}, error={}", output_dir, exc
        )
        return None
    return validated_output_dir / ".capture"


def _load_resumable_capture_manifest(capture_dir: Path) -> Optional[dict[str, object]]:
    sealed_path = capture_dir / "SEALED"
    manifest_path = capture_dir / "manifest.json"
    if not capture_dir.exists() or not sealed_path.exists() or not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "恢复 raw spool 失败，manifest 无法读取: capture_dir={}, error={}", capture_dir, exc
        )
        return None

    if not isinstance(manifest, dict):
        return None
    segments_raw = manifest.get("segments")
    if not isinstance(segments_raw, list) or not segments_raw:
        return None
    segments: list[str] = []
    for segment_name in segments_raw:
        if not isinstance(segment_name, str) or not segment_name:
            return None
        segment_path = capture_dir / segment_name
        if not segment_path.exists() or not segment_path.is_file():
            return None
        segments.append(segment_name)

    raw_bytes_obj = manifest.get("raw_bytes")
    raw_frame_count_obj = manifest.get("raw_frame_count")
    end_time_raw = manifest.get("end_time")
    if not isinstance(end_time_raw, str):
        return None
    try:
        sealed_at = datetime.fromisoformat(end_time_raw)
    except ValueError:
        return None
    try:
        if raw_bytes_obj is not None and not isinstance(raw_bytes_obj, (int, float, str)):
            return None
        if raw_frame_count_obj is not None and not isinstance(
            raw_frame_count_obj, (int, float, str)
        ):
            return None
        raw_bytes_value = raw_bytes_obj if isinstance(raw_bytes_obj, (int, float, str)) else 0
        raw_frame_count_value = (
            raw_frame_count_obj if isinstance(raw_frame_count_obj, (int, float, str)) else 0
        )
        resolved_raw_bytes = int(raw_bytes_value or 0)
        resolved_raw_frame_count = int(raw_frame_count_value or 0)
    except (TypeError, ValueError):
        return None
    if resolved_raw_bytes < 0 or resolved_raw_frame_count < 0:
        return None

    return {
        "segments": segments,
        "raw_bytes": resolved_raw_bytes,
        "raw_frame_count": resolved_raw_frame_count,
        "sealed_at": sealed_at,
    }


def _resume_collecting_record_from_sealed_spool(
    record: CollectionRecord, storage_base: Path
) -> bool:
    capture_dir = _resolve_capture_dir_from_output_dir(
        output_dir=record.output_dir,
        storage_base=storage_base,
    )
    if capture_dir is None:
        return False
    manifest = _load_resumable_capture_manifest(capture_dir)
    if manifest is None:
        return False

    record.collection_status = CollectionRecordStatusEnum.FINALIZING.value
    record.raw_capture_dir = str(capture_dir)
    record.raw_bytes = cast(int, manifest["raw_bytes"])
    record.raw_frame_count = cast(int, manifest["raw_frame_count"])
    sealed_at = cast(datetime, manifest["sealed_at"])
    record.spool_sealed_at = sealed_at
    record.end_time = sealed_at
    return True


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
            try:
                capture_dir = validate_safe_path(
                    record.raw_capture_dir,
                    allowed_base=context.settings.storage.base_path,
                    allow_relative=False,
                )
            except PathValidationError as exc:
                if record.collection_status == CollectionRecordStatusEnum.FINALIZING.value:
                    record.collection_status = CollectionRecordStatusEnum.FINALIZE_FAILED.value
                    record.materialize_error = f"恢复待整理任务失败：raw spool 路径非法 ({exc})"
                logger.warning("待整理记录路径非法，标记或保留失败状态: record_id={}", record.id)
                continue
            if _load_resumable_capture_manifest(capture_dir) is None:
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


async def _scan_pending_cloud_notify(context: AppContext) -> None:
    """启动时扫描待云端通知的记录，仅记录日志，不自动调用云端接口。"""
    async with context.services.db_session_factory() as session:
        result = await session.execute(
            select(CollectionRecord).where(
                CollectionRecord.upload_status == UploadStatus.COMPLETED.value,
                CollectionRecord.cloud_id.is_(None),
            )
        )
        records = result.scalars().all()
        eligible = []
        rejected_ids = []
        for record in records:
            try:
                context.services.upload_service.validate_record_ready_for_cloud_notification(record)
            except Exception:
                rejected_ids.append(record.id)
                continue
            eligible.append(record)
        if eligible:
            logger.warning(
                f"发现 {len(eligible)} 条文件已上传但未通知云端的数据，"
                f"请登录后通过 /api/upload/reconcile-cloud-notify 补偿"
            )
        else:
            logger.info("启动扫描：无待云端通知的记录")
        if rejected_ids:
            logger.warning(
                "发现 {} 条已上传但不满足云端通知条件的脏记录: {}", len(rejected_ids), rejected_ids
            )


async def _recover_interrupted_uploads(context: AppContext) -> None:
    """恢复强制重启后残留的上传/云端通知状态。"""
    async with context.services.db_session_factory() as session:
        uploading_result = await session.execute(
            select(CollectionRecord).where(
                CollectionRecord.upload_status == UploadStatus.UPLOADING.value
            )
        )
        uploading_records = uploading_result.scalars().all()
        for record in uploading_records:
            record.upload_status = UploadStatus.INTERRUPTED.value

        notifying_result = await session.execute(
            select(CollectionRecord).where(
                CollectionRecord.cloud_id.is_(None),
                CollectionRecord.cloud_notify_status == CloudNotifyStatus.NOTIFYING.value,
            )
        )
        notifying_records = notifying_result.scalars().all()
        for record in notifying_records:
            record.cloud_notify_status = CloudNotifyStatus.FAILED.value
            record.cloud_notify_error = "应用重启时发现云端通知中断"

        if uploading_records or notifying_records:
            await session.commit()
        if uploading_records:
            logger.warning(
                "启动恢复：将 {} 条残留 uploading 记录标记为 interrupted", len(uploading_records)
            )
        if notifying_records:
            logger.warning(
                "启动恢复：将 {} 条残留 notifying 记录标记为 failed", len(notifying_records)
            )


async def _check_zmq_startup_health(context: AppContext) -> None:
    """启动时检查 collection 和 monitor 数据源是否就绪"""
    collection_ready = await context.services.zeromq_consumer.wait_until_collection_ready(
        timeout=5.0
    )
    if not collection_ready:
        logger.error("启动检查失败: robotos_collection 在 5 秒内未收到数据")

    monitor_service = context.services.monitor_service
    if not monitor_service.is_cache_ready():
        logger.error("启动检查失败: robotos_command monitor 命令未返回有效数据")
