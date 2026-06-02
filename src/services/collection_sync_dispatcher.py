"""采集完成后的上传与云端同步调度"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from src.core.context import get_current_token
from src.models.database import CollectionRecord
from src.schemas.upload import UploadStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.core.task_manager import BackgroundTaskManager
    from src.services.cloud_client import CloudClient
    from src.services.upload_service import UploadService


class CollectionSyncDispatcher:
    """采集后续同步调度器"""

    def __init__(
        self,
        *,
        task_manager: "BackgroundTaskManager | None",
        upload_service: "UploadService | None",
        session_maker: "async_sessionmaker | None",
        cloud_client: "CloudClient | None",
        storage_path,
        remote_path: str,
    ) -> None:
        self._task_manager = task_manager
        self._upload_service = upload_service
        self._session_maker = session_maker
        self._cloud_client = cloud_client
        self._storage_path = storage_path
        self._remote_path = remote_path

    def schedule(self, record: CollectionRecord) -> None:
        """调度上传与同步任务"""
        task_id = f"cloud_sync_{record.id}_{datetime.now().timestamp()}"
        token = get_current_token()
        if self._task_manager:
            self._task_manager.create_task(
                task_id=task_id,
                coro=self._upload_and_sync(record, token=token),
                critical=True,
            )
            logger.debug(f"云端同步任务已调度: {task_id}")
            return

        logger.warning(f"TaskManager 未配置，云端同步任务将不受管理: {task_id}")
        asyncio.create_task(self._upload_and_sync(record, token=token))

    async def _upload_and_sync(self, record: CollectionRecord, *, token: str | None = None) -> None:
        """先执行上传，失败时回退到仅通知云端"""
        if not self._upload_service or not self._session_maker:
            logger.error("未配置上传服务或数据库会话工厂，无法执行采集后同步")
            return

        try:
            async with self._session_maker() as db:
                progress = await self._upload_service.upload_single(
                    record.id,
                    db,
                    cloud_notify_session_factory=self._session_maker,
                    cloud_notify_token=token,
                )
                if progress.status == UploadStatus.COMPLETED:
                    logger.info(f"采集数据上传成功: record_id={record.id}")
                else:
                    logger.error(
                        f"采集数据上传失败: record_id={record.id}, error={progress.error_message}"
                    )
        except Exception as exc:
            logger.error(f"采集数据上传异常: record_id={record.id}, error={exc}")
