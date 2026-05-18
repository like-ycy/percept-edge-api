"""数据采集服务"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.core.exceptions import BusinessError
from src.core.path_validator import sanitize_filename
from src.models.database import CollectionRecord, now_shanghai, Task
from src.schemas.auth import UserInfo
from src.schemas.collection import CollectionSession, CollectionStatusEnum
from src.services.collection_materialization_dispatcher import CollectionMaterializationDispatcher
from src.services.collection_record_store import CollectionRecordStore
from src.services.collection_validation_dispatcher import CollectionValidationDispatcher
from src.services.raw_frame_spool import RawFrameSpoolWriter
from src.services.zeromq_consumer import ZeroMQConsumer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.core.task_manager import BackgroundTaskManager
    from src.services.cloud_client import CloudClient
    from src.services.collection_lock_service import CollectionLockService
    from src.services.monitor_service import MonitorService
    from src.services.upload_service import UploadService


class CollectionService:
    """数据采集服务（应用级单例）"""

    def __init__(
        self,
        consumer: ZeroMQConsumer,
        settings: Settings,
        task_manager: "BackgroundTaskManager | None" = None,
        cloud_client: "CloudClient | None" = None,
        monitor_service: "MonitorService | None" = None,
        lock_service: "CollectionLockService | None" = None,
    ):
        self.consumer = consumer
        self.storage_path = Path(settings.storage.base_path)
        self._session: CollectionSession | None = None
        self._collecting = False
        self._output_dir: Path | None = None
        self._task_manager = task_manager
        self._cloud_client = cloud_client
        self._monitor_service = monitor_service
        self._lock_service = lock_service

        self._start_time: datetime | None = None
        self._task_id: int | None = None
        self._task_name: str | None = None
        self._user_id: int | None = None
        self._user_name: str | None = None
        self._record_id: int | None = None
        self._spool_writer: RawFrameSpoolWriter | None = None

        self._remote_path = settings.upload.remote_path
        self._upload_service: "UploadService | None" = None
        self._session_maker: "async_sessionmaker | None" = None
        self._record_store = CollectionRecordStore()
        self._validation_dispatcher_instance: CollectionValidationDispatcher | None = None
        self._materialization_dispatcher_instance: CollectionMaterializationDispatcher | None = None
        self._frame_drop_threshold = settings.collection.frame_drop_threshold
        self._video_only = settings.collection.video_only
        self.consumer.set_collection_sink_failure_handler(self._on_collection_sink_failure)

    @property
    def is_collecting(self) -> bool:
        return self._collecting

    def set_task_manager(self, task_manager: "BackgroundTaskManager") -> None:
        self._task_manager = task_manager

    def set_upload_service(
        self, upload_service: "UploadService", session_maker: "async_sessionmaker"
    ) -> None:
        self._upload_service = upload_service
        self._session_maker = session_maker
        self._validation_dispatcher_instance = None
        self._materialization_dispatcher_instance = None

    def schedule_validation(self, record_id: int, *, upload_after_success: bool = True) -> bool:
        return self._validation_dispatcher.schedule(
            record_id,
            upload_after_success=upload_after_success,
        )

    def schedule_materialization(
        self,
        record_id: int,
        *,
        validation_upload_after_success: bool = True,
    ) -> bool:
        return self._materialization_dispatcher.schedule(
            record_id,
            validation_upload_after_success=validation_upload_after_success,
        )

    def get_status(self) -> CollectionSession | None:
        return self._session

    async def start_collection(
        self, task_id: int, user: UserInfo, db: AsyncSession
    ) -> CollectionSession:
        if self._collecting:
            raise BusinessError("采集已在进行中")

        if self._lock_service and await self._lock_service.is_locked():
            state = await self._lock_service.get_state()
            reason = state.reason or "未知原因"
            logger.error(
                "采集请求被全局锁拦截: reason={}, triggered_record_id={}, triggered_at={}",
                reason,
                state.triggered_record_id,
                state.triggered_at,
            )
            raise BusinessError(
                f"采集已锁定：{reason}（触发记录 #{state.triggered_record_id}，"
                f"时间 {state.triggered_at}）。"
                "请运维执行解锁脚本后重试。"
            )

        await self._ensure_collection_sources_ready()
        self._collecting = True

        try:
            result = await db.execute(
                select(Task).where(Task.task_id == task_id, Task.is_deleted.is_(False))
            )
            task = result.scalar_one_or_none()
            if not task:
                raise BusinessError(f"任务不存在: task_id={task_id}")
            if task.status not in ["run", "pending"]:
                raise BusinessError(
                    f"任务状态不是 run 或 pending，无法开始采集: 当前状态={task.status}"
                )
            if task.status == "completed":
                raise BusinessError("任务已完成，无法重新采集")

            if self._video_only:
                logger.info("video_only 模式跳过任务设备类型与 monitor 校验: task_id={}", task_id)
            elif task.device_type_name and self._monitor_service:
                robot_status = self._monitor_service.get_robot_status()
                if robot_status:
                    current_device_type = robot_status.metadata.robot_model
                    if current_device_type and current_device_type != task.device_type_name:
                        raise BusinessError(
                            f"设备类型不匹配: 任务要求 {task.device_type_name}，当前设备类型 {current_device_type}"
                        )
            else:
                raise BusinessError("任务未指定设备类型，无法开始采集")
        except BusinessError:
            self._collecting = False
            raise

        self._start_time = now_shanghai()
        self._task_id = task_id
        self._user_id = user.user_id
        self._user_name = user.user_name
        self._task_name = task.task_name or task.template_name

        safe_user_name = sanitize_filename(self._user_name)
        safe_task_name = sanitize_filename(self._task_name)
        self._output_dir = self._build_output_dir(
            safe_user_name=safe_user_name,
            safe_task_name=safe_task_name,
        )
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._session = CollectionSession(
            task_id=task_id,
            status=CollectionStatusEnum.COLLECTING,
            start_time=self._start_time,
            frame_count=0,
            output_dir=str(self._output_dir),
        )

        metadata = None
        robot_name = ""
        if self._monitor_service:
            metadata = self._monitor_service.build_metadata()
            robot_name = metadata.robot_name or ""

        record = await self._record_store.create_collecting_record(
            db=db,
            task_id=task_id,
            user_id=user.user_id,
            user_name=user.user_name,
            start_time=self._start_time,
            output_dir=self._output_dir,
        )
        self._record_id = record.id

        spool_writer = RawFrameSpoolWriter(
            output_dir=self._output_dir,
            start_time=self._start_time,
            fps=30,
            record_id=record.id,
            metadata_snapshot=metadata.model_dump() if metadata else {},
            collector_name=self._user_name,
            task_name=self._task_name or "",
            robot_name=robot_name,
            capture_mode="video_only" if self._video_only else "standard",
        )
        spool_writer.start()
        self.consumer.attach_collection_sink(spool_writer)
        await self.consumer.enable_collection_queue()
        self._spool_writer = spool_writer
        return self._session

    def _build_output_dir(self, *, safe_user_name: str, safe_task_name: str) -> Path:
        assert self._start_time is not None
        date_dir = f"data{self._start_time.strftime('%Y%m%d')}"
        base_task_dir = f"{safe_task_name}_{self._start_time.strftime('%Y%m%d%H%M%S')}"
        base_dir = self.storage_path / date_dir / safe_user_name

        candidate = base_dir / base_task_dir
        suffix = 1
        while self._should_rotate_output_dir(candidate):
            candidate = base_dir / f"{base_task_dir}_retry{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _should_rotate_output_dir(output_dir: Path) -> bool:
        if not output_dir.exists():
            return False

        capture_dir = output_dir / ".capture"
        if not capture_dir.exists():
            return False

        return not (capture_dir / "SEALED").exists()

    async def _ensure_collection_sources_ready(self) -> None:
        collection_ready = self.consumer.has_recent_collection_data(max_age_seconds=2.0)
        if not collection_ready:
            collection_ready = await self.consumer.wait_until_collection_ready(timeout=1.0)
        if not collection_ready:
            logger.error("开始采集失败: robotos_collection 无可用数据")
            raise BusinessError("无法获取采集数据，请检查 robotos_collection 数据流")

        if self._video_only:
            return

        if self._monitor_service is None:
            logger.error("开始采集失败: MonitorService 未配置")
            raise BusinessError("无法获取采集数据，请检查 robotos_monitor 数据流")

        system_info = self._monitor_service.get_system_info()
        robot_status = self._monitor_service.get_robot_status()
        if system_info is None or robot_status is None:
            logger.error("开始采集失败: robotos_monitor 无有效数据")
            raise BusinessError("无法获取采集数据，请检查 robotos_monitor 数据流")

    async def stop_collection(self, db: AsyncSession) -> CollectionSession:
        if not self._collecting:
            if self._session and self._session.status == CollectionStatusEnum.STOPPING:
                logger.info(
                    "重复停止采集请求，返回当前 STOPPING 会话: task_id={}", self._session.task_id
                )
                return self._session
            raise BusinessError("没有正在进行的采集")

        assert self._session is not None
        assert self._spool_writer is not None
        assert self._record_id is not None
        self._session.status = CollectionStatusEnum.STOPPING
        self._collecting = False

        try:
            stop_wait_started_at = perf_counter()
            self._spool_writer.stop_accepting()
            self.consumer.detach_collection_sink()
            self.consumer.disable_collection_queue()
            await asyncio.to_thread(self._spool_writer.wait_until_drained, 30.0)
            logger.info(
                "停止采集等待 raw spool drain 完成: task_id={}, record_id={}, duration_ms={:.1f}",
                self._session.task_id,
                self._record_id,
                (perf_counter() - stop_wait_started_at) * 1000,
            )

            seal_started_at = perf_counter()
            seal_time = now_shanghai()
            seal_result = await asyncio.to_thread(self._spool_writer.seal, seal_time)
            logger.info(
                "停止采集 seal 完成: task_id={}, record_id={}, frames={}, bytes={}, duration_ms={:.1f}",
                self._session.task_id,
                self._record_id,
                seal_result.raw_frame_count,
                seal_result.raw_bytes,
                (perf_counter() - seal_started_at) * 1000,
            )
            self._session.frame_count = seal_result.raw_frame_count

            record = await db.get(CollectionRecord, self._record_id)
            if record is None:
                raise BusinessError(f"采集记录不存在: record_id={self._record_id}")
            await self._record_store.mark_record_finalizing(
                db=db,
                record=record,
                end_time=seal_time,
                raw_capture_dir=seal_result.capture_dir,
                raw_bytes=seal_result.raw_bytes,
                raw_frame_count=seal_result.raw_frame_count,
                spool_sealed_at=seal_result.sealed_at,
            )

            if record.task_id:
                await self._update_task_progress(db, record.task_id)

            scheduled = self.schedule_materialization(record.id)
            if scheduled:
                logger.info(
                    "采集记录已加入整理队列: task_id={}, record_id={}",
                    self._session.task_id,
                    record.id,
                )
            else:
                logger.warning(
                    "采集记录整理调度失败: task_id={}, record_id={}",
                    self._session.task_id,
                    record.id,
                )

            self._reset_runtime_state()
            self._session.status = CollectionStatusEnum.IDLE
            self._session.error_message = None
            return self._session
        except Exception as exc:
            logger.exception("停止采集失败")
            self._mark_session_error(exc)
            await self._cleanup_on_error(mark_record_aborted=True)
            raise

    async def discard_collection(self, db: AsyncSession) -> CollectionSession:
        if not self._collecting:
            raise BusinessError("没有正在进行的采集")

        assert self._session is not None
        self._session.status = CollectionStatusEnum.STOPPING
        self._collecting = False

        self.consumer.detach_collection_sink()
        self.consumer.disable_collection_queue()
        if self._spool_writer:
            self._spool_writer.abort()

        if self._output_dir and self._output_dir.exists():
            shutil.rmtree(self._output_dir)
            logger.info("已删除采集目录: {}", self._output_dir)

        if self._record_id is not None:
            record = await db.get(CollectionRecord, self._record_id)
            if record is not None:
                await self._record_store.delete_record(db=db, record=record)

        self._reset_runtime_state()
        self._session.status = CollectionStatusEnum.IDLE
        return self._session

    async def _on_collection_sink_failure(self, exc: Exception) -> None:
        logger.error("采集 raw sink 故障，停止当前采集: {}", exc)
        self._collecting = False
        self._mark_session_error(exc)
        await self._cleanup_on_error(mark_record_aborted=True)

    async def _cleanup_on_error(self, *, mark_record_aborted: bool = False) -> None:
        logger.warning("正在清理采集资源...")
        try:
            self.consumer.detach_collection_sink()
            self.consumer.disable_collection_queue()
        except Exception as exc:
            logger.error("禁用采集队列失败: {}", exc)

        if self._spool_writer:
            self._spool_writer.abort()

        if mark_record_aborted and self._session_maker and self._record_id is not None:
            try:
                async with self._session_maker() as db:
                    record = await db.get(CollectionRecord, self._record_id)
                    if record is not None:
                        await self._record_store.mark_record_aborted(
                            db=db,
                            record=record,
                            end_time=now_shanghai(),
                        )
            except Exception as exc:
                logger.error("更新异常中止记录失败: {}", exc)

        self._reset_runtime_state()
        logger.warning("采集资源清理完成")

    async def _update_task_progress(self, db: AsyncSession, task_id: int) -> None:
        await self._record_store.update_task_progress(db, task_id)

    def _mark_session_error(self, exc: Exception) -> None:
        if self._session is None:
            return
        self._session.status = CollectionStatusEnum.ERROR
        self._session.error_message = f"{type(exc).__name__}: {exc}"

    def _reset_runtime_state(self) -> None:
        self._spool_writer = None
        self._output_dir = None
        self._start_time = None
        self._task_id = None
        self._task_name = None
        self._user_id = None
        self._user_name = None
        self._record_id = None

    @property
    def _validation_dispatcher(self) -> CollectionValidationDispatcher:
        if self._validation_dispatcher_instance is None:
            self._validation_dispatcher_instance = CollectionValidationDispatcher(
                task_manager=self._task_manager,
                session_maker=self._session_maker,
                monitor_service=self._monitor_service,
                upload_service=self._upload_service,
                cloud_client=self._cloud_client,
                storage_path=self.storage_path,
                remote_path=self._remote_path,
                lock_service=self._lock_service,
                frame_drop_threshold=self._frame_drop_threshold,
            )
        return self._validation_dispatcher_instance

    @property
    def _materialization_dispatcher(self) -> CollectionMaterializationDispatcher:
        if self._materialization_dispatcher_instance is None:
            self._materialization_dispatcher_instance = CollectionMaterializationDispatcher(
                task_manager=self._task_manager,
                session_maker=self._session_maker,
                collection_service=self,
            )
        return self._materialization_dispatcher_instance
