"""上传业务逻辑层"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenacity import (
    AsyncRetrying,
    RetryError,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.config import Settings
from src.core.context import get_current_token
from src.core.task_manager import BackgroundTaskManager
from src.core.exceptions import BusinessError, NotFoundError
from src.models.database import CollectionRecord, now_shanghai
from src.schemas.collection import CollectionRecordStatusEnum
from src.schemas.status import CloudNotifyStatus
from src.schemas.upload import UploadProgress, UploadResult, UploadStatus
from src.schemas.validation import ValidationStatusEnum
from src.services.cloud_client import CloudClient
from src.services.rsync_uploader import RsyncUploader
from src.services.upload_notifier import UploadNotifier
from src.services.upload_progress_store import UploadProgressStore
from src.services.upload_record_store import UploadRecordStore


class UploadService:
    """上传服务（应用级单例）

    提供基于 rsync 的采集数据上传功能。
    进度信息存储在实例中，跨请求共享。
    """

    def __init__(
        self,
        settings: Settings,
        cloud_client: CloudClient,
        task_manager: BackgroundTaskManager | None = None,
    ):
        """初始化上传服务

        Args:
            settings: 应用配置
            cloud_client: 云端客户端
        """
        self.remote_user = settings.upload.remote_user
        self.remote_host = settings.upload.remote_host
        self.remote_port = settings.upload.remote_port
        self.remote_path = settings.upload.remote_path
        self.ssh_key = settings.upload.ssh_key_path
        self.max_retries = settings.upload.max_retries
        self._storage_base = Path(settings.storage.base_path).resolve()
        self._task_manager = task_manager
        self._record_store = UploadRecordStore()
        self._progress_store = UploadProgressStore()
        self._cloud_notify_tasks: set[asyncio.Task[None]] = set()
        self._scheduled_batch_uploads: dict[str, tuple[int, ...]] = {}
        self._notifier = UploadNotifier(self._storage_base, self.remote_path, cloud_client)
        self._rsync_uploader = RsyncUploader(
            remote_user=self.remote_user,
            remote_host=self.remote_host,
            remote_port=self.remote_port,
            remote_path=self.remote_path,
            ssh_key=self.ssh_key,
            storage_base=self._storage_base,
            progress_store=self._progress_store,
            record_store=self._record_store,
        )

    async def get_record_by_id(self, record_id: int, db: AsyncSession) -> CollectionRecord | None:
        """根据 ID 获取采集记录"""
        return await self._record_store.get_record_by_id(record_id, db)

    async def get_record_by_id_for_user(
        self, record_id: int, user_id: int, db: AsyncSession
    ) -> CollectionRecord | None:
        """根据 ID 和用户 ID 获取采集记录。"""
        return await self._record_store.get_record_by_id_for_user(record_id, user_id, db)

    async def get_progress_for_user(
        self, record_id: int, user_id: int, db: AsyncSession
    ) -> UploadProgress | None:
        """获取当前用户可见的上传进度。"""
        record = await self.get_record_by_id_for_user(record_id, user_id, db)
        if record is None:
            return None
        return self.get_progress(record_id)

    async def update_record_upload_status(
        self, record_id: int, status: str, db: AsyncSession
    ) -> None:
        """更新采集记录的上传状态"""
        await self._record_store.update_upload_status(record_id, status, db)

    async def update_record_cloud_id(self, record_id: int, cloud_id: int, db: AsyncSession) -> None:
        """更新采集记录关联的云端数据 ID。"""
        await self._record_store.update_cloud_id(record_id, cloud_id, db)

    async def upload_single(
        self,
        record_id: int,
        db: AsyncSession,
        cloud_notify_session_factory: async_sessionmaker[AsyncSession] | None = None,
        cloud_notify_token: str | None = None,
    ) -> UploadProgress:
        """上传单条记录

        Args:
            record_id: 采集记录 ID
            db: 数据库会话

        Returns:
            上传进度信息

        Raises:
            ValueError: 记录不存在
        """
        record = await self.get_record_by_id(record_id, db)
        if not record:
            raise NotFoundError("记录", str(record_id))

        terminal_progress = self._build_completed_progress(record_id)
        if record.upload_status == UploadStatus.COMPLETED.value:
            logger.info(
                "采集记录已处于上传完成状态，跳过重复 rsync: record_id={}, cloud_id={}, progress={}",
                record_id,
                record.cloud_id,
                record.upload_progress,
            )
            self._progress_store._progress[record_id] = terminal_progress
            if record.cloud_id is None and cloud_notify_session_factory is not None:
                self.validate_record_ready_for_cloud_notification(record)
                self.schedule_cloud_notification_retry(
                    record,
                    cloud_notify_session_factory,
                    token=cloud_notify_token,
                )
            return terminal_progress

        local_path, local_files = self.resolve_record_upload_inputs(record)

        progress = self._progress_store.start(record_id)
        await self.update_record_upload_status(record_id, UploadStatus.UPLOADING.value, db)

        upload_start_time = datetime.now(timezone.utc)
        success = await self._upload_with_retry(
            local_path,
            progress,
            record_id,
            db,
            local_files=local_files,
        )
        upload_end_time = datetime.now(timezone.utc)

        if success:
            await self._record_store.update_upload_window(
                record_id,
                upload_start_time,
                upload_end_time,
                db,
            )
            progress = self._progress_store.mark_completed(record_id) or progress
            await self.update_record_upload_status(record_id, UploadStatus.COMPLETED.value, db)
            await self._record_store.update_upload_progress(record_id, 100, db)
            self._schedule_cloud_notification(
                record_id=record_id,
                record=record,
                upload_start_time=upload_start_time,
                upload_end_time=upload_end_time,
                session_factory=cloud_notify_session_factory,
                token=cloud_notify_token,
            )
        else:
            if progress.status == UploadStatus.INTERRUPTED:
                await self.update_record_upload_status(
                    record_id, UploadStatus.INTERRUPTED.value, db
                )
            else:
                progress = self._progress_store.mark_failed(record_id) or progress
                await self.update_record_upload_status(record_id, UploadStatus.FAILED.value, db)

        return progress

    def resolve_record_upload_inputs(self, record: CollectionRecord) -> tuple[str, list[str]]:
        """校验记录是否可以进入 rsync，并返回输出目录与最终产物清单。"""
        if not record.output_dir:
            raise BusinessError(f"记录 {record.id} 没有输出目录")
        self._ensure_record_ready_for_upload(record)
        return record.output_dir, self._resolve_materialized_files(record)

    def validate_record_ready_for_cloud_notification(self, record: CollectionRecord) -> None:
        """校验记录是否允许补偿通知云端。"""
        if record.upload_status != UploadStatus.COMPLETED.value:
            raise BusinessError(f"记录 {record.id} 尚未上传成功，无法通知云端")
        if record.task_id is None:
            raise BusinessError(f"记录 {record.id} 缺少 task_id，无法通知云端")
        self.resolve_record_upload_inputs(record)

    @staticmethod
    def _ensure_record_ready_for_upload(record: CollectionRecord) -> None:
        """上传只能基于已整理并校验通过的最终产物目录。"""
        if record.collection_status != CollectionRecordStatusEnum.COMPLETED.value:
            raise BusinessError(
                f"记录 {record.id} 尚未完成采集整理与校验，当前状态: {record.collection_status}"
            )
        if record.validation_status != ValidationStatusEnum.SUCCESS.value:
            raise BusinessError(
                f"记录 {record.id} 尚未通过数据完整性校验，当前校验状态: {record.validation_status}"
            )

    @staticmethod
    def _resolve_materialized_files(record: CollectionRecord) -> list[str]:
        """读取后台整理阶段记录的最终产物文件清单。"""
        if not record.files:
            raise BusinessError(f"记录 {record.id} 缺少已整理的成品文件列表")
        try:
            files = json.loads(record.files)
        except (json.JSONDecodeError, TypeError) as exc:
            raise BusinessError(f"记录 {record.id} 的成品文件列表格式错误") from exc
        if (
            not isinstance(files, list)
            or not files
            or not all(isinstance(file, str) for file in files)
        ):
            raise BusinessError(f"记录 {record.id} 的成品文件列表为空或格式错误")
        return files

    @staticmethod
    def _build_completed_progress(record_id: int) -> UploadProgress:
        return UploadProgress(
            record_id=record_id,
            status=UploadStatus.COMPLETED,
            progress=100,
        )

    async def upload_single_in_background(
        self,
        record_id: int,
        session_factory: async_sessionmaker[AsyncSession],
        cloud_notify_token: str | None = None,
    ) -> UploadProgress:
        """后台上传单条记录时使用独立短生命周期会话"""
        async with session_factory() as db:
            return await self.upload_single(
                record_id,
                db,
                cloud_notify_session_factory=session_factory,
                cloud_notify_token=cloud_notify_token,
            )

    def schedule_upload_single(
        self,
        record_id: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> bool:
        task_id = f"upload_{record_id}_{datetime.now(timezone.utc).timestamp()}"
        coro = self.upload_single_in_background(
            record_id,
            session_factory,
            cloud_notify_token=get_current_token(),
        )
        if self._task_manager:
            task = self._task_manager.create_task(task_id=task_id, coro=coro, critical=True)
            if task is None:
                coro.close()
                logger.warning(f"上传任务调度失败: record_id={record_id}")
                return False
            return True

        asyncio.create_task(coro)
        return True

    async def upload_batch(
        self,
        record_ids: list[int],
        db: AsyncSession,
        cloud_notify_session_factory: async_sessionmaker[AsyncSession] | None = None,
        cloud_notify_token: str | None = None,
    ) -> UploadResult:
        """批量上传（串行执行）

        Args:
            record_ids: 采集记录 ID 列表
            db: 数据库会话

        Returns:
            上传结果
        """
        success, failed = [], []
        for record_id in record_ids:
            if self._progress_store.consume_cancelled(record_id):
                failed.append(record_id)
                continue

            try:
                result = await self.upload_single(
                    record_id,
                    db,
                    cloud_notify_session_factory=cloud_notify_session_factory,
                    cloud_notify_token=cloud_notify_token,
                )
                if result.status == UploadStatus.COMPLETED:
                    success.append(record_id)
                else:
                    failed.append(record_id)
            except Exception as e:
                logger.error(f"上传 {record_id} 失败: {e}")
                failed.append(record_id)

        return UploadResult(success=success, failed=failed)

    async def upload_batch_in_background(
        self,
        record_ids: list[int],
        session_factory: async_sessionmaker[AsyncSession],
        cloud_notify_token: str | None = None,
    ) -> UploadResult:
        """后台批量上传时使用独立短生命周期会话"""
        async with session_factory() as db:
            return await self.upload_batch(
                record_ids,
                db,
                cloud_notify_session_factory=session_factory,
                cloud_notify_token=cloud_notify_token,
            )

    def schedule_upload_batch(
        self,
        record_ids: list[int],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> bool:
        task_id = f"upload_batch_{datetime.now(timezone.utc).timestamp()}"
        coro = self.upload_batch_in_background(
            record_ids,
            session_factory,
            cloud_notify_token=get_current_token(),
        )
        self._scheduled_batch_uploads[task_id] = tuple(record_ids)
        if self._task_manager:
            task = self._task_manager.create_task(task_id=task_id, coro=coro, critical=True)
            if task is None:
                coro.close()
                self._scheduled_batch_uploads.pop(task_id, None)
                logger.warning(f"批量上传任务调度失败: record_ids={record_ids}")
                return False
            task.add_done_callback(lambda _task: self._scheduled_batch_uploads.pop(task_id, None))
            return True

        task = asyncio.create_task(coro)
        task.add_done_callback(lambda _task: self._scheduled_batch_uploads.pop(task_id, None))
        return True

    async def get_active_uploads(
        self, db: AsyncSession | None = None, user_id: int | None = None
    ) -> list[dict]:
        """获取当前活跃的上传任务"""
        active = []
        allowed_record_ids: set[int] | None = None

        if user_id is not None and db is None:
            return active

        if db is not None and user_id is not None:
            result = await db.execute(
                select(CollectionRecord.id).where(CollectionRecord.user_id == user_id)
            )
            allowed_record_ids = set(result.scalars().all())

        def can_include(record_id: int) -> bool:
            return allowed_record_ids is None or record_id in allowed_record_ids

        for record_id, progress in self._progress_store._progress.items():
            if progress.status == UploadStatus.UPLOADING and can_include(record_id):
                active.append(
                    {
                        "record_id": record_id,
                        "status": progress.status.value,
                        "progress": progress.progress,
                        "source": "memory",
                    }
                )

        if db is not None:
            conditions = [CollectionRecord.upload_status == UploadStatus.UPLOADING.value]
            if user_id is not None:
                conditions.append(CollectionRecord.user_id == user_id)
            result = await db.execute(select(CollectionRecord).where(*conditions))
            for record in result.scalars().all():
                if not any(a["record_id"] == record.id for a in active):
                    active.append(
                        {
                            "record_id": record.id,
                            "status": UploadStatus.UPLOADING.value,
                            "progress": int(record.upload_progress or 0),
                            "source": "database",
                        }
                    )

        if self._task_manager:
            for task_id in self._task_manager.get_tasks_by_prefix("upload_"):
                parts = task_id.split("_")
                if len(parts) >= 2 and parts[1].isdigit():
                    record_id = int(parts[1])
                    if can_include(record_id) and not any(
                        a["record_id"] == record_id for a in active
                    ):
                        active.append(
                            {
                                "record_id": record_id,
                                "status": "scheduled",
                                "progress": 0,
                                "source": "task_manager",
                            }
                        )
            for task_id in self._task_manager.get_tasks_by_prefix("cloud_sync_"):
                parts = task_id.split("_")
                if len(parts) >= 3 and parts[2].isdigit():
                    record_id = int(parts[2])
                    if can_include(record_id) and not any(
                        a["record_id"] == record_id for a in active
                    ):
                        active.append(
                            {
                                "record_id": record_id,
                                "status": "scheduled",
                                "progress": 0,
                                "source": "task_manager",
                            }
                        )

        for record_ids in self._scheduled_batch_uploads.values():
            for record_id in record_ids:
                if can_include(record_id) and not any(a["record_id"] == record_id for a in active):
                    active.append(
                        {
                            "record_id": record_id,
                            "status": "scheduled",
                            "progress": 0,
                            "source": "task_manager",
                        }
                    )

        return active

    def schedule_cloud_notification_retry(
        self,
        record: CollectionRecord,
        session_factory: async_sessionmaker[AsyncSession],
        token: str | None = None,
    ) -> None:
        """补偿调度已上传但未写回 cloud_id 的云端通知。"""
        self.validate_record_ready_for_cloud_notification(record)
        if record.cloud_id is not None:
            logger.info("采集记录已有 cloud_id，跳过云端通知补偿: record_id={}", record.id)
            return
        if record.cloud_notify_status == CloudNotifyStatus.NOTIFYING.value:
            logger.info("采集记录云端通知已在进行中，跳过重复调度: record_id={}", record.id)
            return
        upload_start_time = record.upload_started_at or record.updated_at
        upload_end_time = record.upload_finished_at or record.updated_at
        if upload_start_time is None or upload_end_time is None:
            raise BusinessError(f"记录 {record.id} 缺少上传时间戳，无法通知云端")
        self._schedule_cloud_notification(
            record_id=record.id,
            record=record,
            upload_start_time=upload_start_time,
            upload_end_time=upload_end_time,
            session_factory=session_factory,
            token=token,
        )

    async def _upload_with_retry(
        self,
        local_path: str,
        progress: UploadProgress,
        record_id: int,
        db: AsyncSession,
        *,
        local_files: Optional[list[str]] = None,
    ) -> bool:
        """带重试的上传（使用 tenacity 实现指数退避 + 抖动）

        Args:
            local_path: 本地路径
            progress: 进度对象
            record_id: 采集记录 ID
            db: 数据库会话

        Returns:
            是否成功
        """
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential_jitter(initial=1, max=60, jitter=5),
                reraise=True,
            ):
                with attempt:
                    # 检查是否被取消
                    if self._progress_store.is_cancelled(progress.record_id):
                        self._progress_store.set_error_message(record_id, "用户取消")
                        raise asyncio.CancelledError("用户取消上传")

                    self._progress_store.mark_retry(progress, attempt.retry_state.attempt_number)
                    try:
                        await self._rsync_uploader.upload(
                            local_path,
                            record_id,
                            db,
                            local_files=local_files,
                        )
                    except Exception as e:
                        self._progress_store.set_error_message(record_id, str(e))
                        logger.warning(
                            f"上传重试 {attempt.retry_state.attempt_number}/{self.max_retries}: {e}"
                        )
                        raise
            return True
        except asyncio.CancelledError:
            self._progress_store.mark_interrupted(record_id)
            await self.update_record_upload_status(record_id, UploadStatus.INTERRUPTED.value, db)
            return False
        except RetryError:
            return False

    def get_progress(self, record_id: int) -> UploadProgress | None:
        """获取上传进度

        Args:
            record_id: 采集记录 ID

        Returns:
            进度信息，不存在返回 None
        """
        return self._progress_store.get(record_id)

    def cancel_upload(self, record_id: int) -> None:
        """取消上传

        Args:
            record_id: 采集记录 ID
        """
        self._progress_store.cancel(record_id)

    @property
    def _cancelled(self) -> set[int]:
        """兼容旧测试的取消集合访问"""
        return self._progress_store._cancelled

    async def _notify_cloud(
        self,
        record: CollectionRecord,
        upload_start_time: datetime,
        upload_end_time: datetime,
        *,
        token: str | None = None,
    ) -> int | None:
        """兼容旧接口的上传完成通知代理"""
        return await self._notifier.notify(
            record,
            upload_start_time,
            upload_end_time,
            token=token,
        )

    def _schedule_cloud_notification(
        self,
        *,
        record_id: int,
        record: CollectionRecord,
        upload_start_time: datetime,
        upload_end_time: datetime,
        session_factory: async_sessionmaker[AsyncSession] | None,
        token: str | None = None,
    ) -> None:
        """调度上传完成后的云端通知，避免阻塞上传主流程。"""
        coro = self._notify_cloud_and_update_record(
            record_id=record_id,
            record=record,
            upload_start_time=upload_start_time,
            upload_end_time=upload_end_time,
            session_factory=session_factory,
            token=token if token is not None else get_current_token(),
        )
        if self._task_manager:
            task_id = f"cloud_notify_{record_id}_{datetime.now(timezone.utc).timestamp()}"
            task = self._task_manager.create_task(task_id=task_id, coro=coro, critical=True)
            if task is None:
                coro.close()
                logger.warning(f"云端通知任务调度失败: record_id={record_id}")
            return

        task = asyncio.create_task(coro)
        self._cloud_notify_tasks.add(task)
        task.add_done_callback(self._finalize_cloud_notify_task)

    def _finalize_cloud_notify_task(self, task: asyncio.Task[None]) -> None:
        """清理云端通知后台任务并记录未捕获异常。"""
        self._cloud_notify_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            logger.debug("上传完成云端通知任务已取消")
        except Exception as exc:
            logger.exception(f"上传完成云端通知任务异常: {exc}")

    async def _notify_cloud_and_update_record(
        self,
        *,
        record_id: int,
        record: CollectionRecord,
        upload_start_time: datetime,
        upload_end_time: datetime,
        session_factory: async_sessionmaker[AsyncSession] | None,
        token: str | None,
    ) -> None:
        """通知云端并在成功后写回 cloud_id，失败时记录错误信息。"""
        if session_factory is None:
            logger.warning(f"缺少数据库会话工厂，无法执行云端通知: record_id={record_id}")
            return

        async with session_factory() as db:
            acquired = await self._record_store.mark_cloud_notify_in_progress(record_id, db)
            if not acquired:
                logger.info(f"云端通知无需执行或已在进行中: record_id={record_id}")
                return
            current_record = await self.get_record_by_id(record_id, db)
            if current_record is None:
                logger.error(f"采集记录不存在，无法执行云端通知: record_id={record_id}")
                return
            try:
                self.validate_record_ready_for_cloud_notification(current_record)
            except BusinessError as exc:
                await self._record_store.update_cloud_notify_status(
                    record_id,
                    status=CloudNotifyStatus.FAILED.value,
                    error=str(exc),
                    db=db,
                )
                logger.warning("云端通知前置校验失败: record_id={}, error={}", record_id, exc)
                return

        try:
            cloud_id = await self._notifier.notify(
                record=current_record,
                upload_start_time=upload_start_time,
                upload_end_time=upload_end_time,
                token=token,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"云端通知异常: record_id={record_id}, error={error_msg}")

            async with session_factory() as db:
                await self._record_store.update_cloud_notify_status(
                    record_id,
                    status=CloudNotifyStatus.FAILED.value,
                    error=error_msg,
                    db=db,
                )
            return

        if cloud_id is None:
            error_msg = "云端返回空 cloud_id"
            logger.error(f"{error_msg}: record_id={record_id}")
            async with session_factory() as db:
                await self._record_store.update_cloud_notify_status(
                    record_id,
                    status=CloudNotifyStatus.FAILED.value,
                    error=error_msg,
                    db=db,
                )
            return

        # 通知成功：写回 cloud_id 和通知状态
        async with session_factory() as db:
            await self.update_record_cloud_id(record_id, cloud_id, db)
            await self._record_store.update_cloud_notify_status(
                record_id,
                status=CloudNotifyStatus.COMPLETED.value,
                clear_error=True,
                notified_at=now_shanghai(),
                db=db,
            )
        logger.info(f"云端通知成功: record_id={record_id}, cloud_id={cloud_id}")
