"""采集完成后的上传与云端同步调度"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.models.database import CollectionRecord, now_shanghai
from src.schemas.upload import CloudDataCreateRequest

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
        storage_path: Path,
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
        if self._task_manager:
            self._task_manager.create_task(
                task_id=task_id,
                coro=self._upload_and_sync(record),
                critical=True,
            )
            logger.debug(f"云端同步任务已调度: {task_id}")
            return

        logger.warning(f"TaskManager 未配置，云端同步任务将不受管理: {task_id}")
        asyncio.create_task(self._upload_and_sync(record))

    async def _upload_and_sync(self, record: CollectionRecord) -> None:
        """先执行上传，失败时回退到仅通知云端"""
        if not self._upload_service or not self._session_maker:
            logger.warning("未配置上传服务或数据库会话工厂，回退到仅通知云端")
            await self._notify_cloud_only(record)
            return

        try:
            async with self._session_maker() as db:
                progress = await self._upload_service.upload_single(
                    record.id,
                    db,
                    cloud_notify_session_factory=self._session_maker,
                )
                if progress.status.value == "completed":
                    logger.info(f"采集数据上传成功: record_id={record.id}")
                else:
                    logger.error(
                        f"采集数据上传失败: record_id={record.id}, error={progress.error_message}"
                    )
        except Exception as exc:
            logger.error(f"采集数据上传异常: record_id={record.id}, error={exc}")

    async def _notify_cloud_only(self, record: CollectionRecord) -> None:
        """仅通知云端"""
        if not self._cloud_client or not self._session_maker:
            logger.debug("未配置云端客户端，跳过同步")
            return

        if record.task_id is None:
            logger.error("采集记录缺少 task_id，跳过云端同步")
            return

        relative_path = ""
        if record.output_dir:
            output_path = Path(record.output_dir)
            try:
                relative_path = str(output_path.relative_to(self._storage_path))
            except ValueError:
                relative_path = output_path.name
        remote_filepath = f"{self._remote_path}/{relative_path}" if relative_path else ""

        current_time = now_shanghai()
        request_data = CloudDataCreateRequest(
            task_id=record.task_id,
            filepath=remote_filepath,
            collector=record.user_id,
            file_size=record.file_size or 0,
            file_time=record.duration or 0,
            upload_time=int(current_time.timestamp()),
            end_time=int(current_time.timestamp()),
        )

        cloud_id = await self._cloud_client.create_data(request_data)
        if cloud_id is not None:
            async with self._session_maker() as db:
                db_record = await db.get(CollectionRecord, record.id)
                if db_record:
                    db_record.cloud_id = cloud_id
                    await db.commit()
            logger.info(f"云端数据同步成功: record_id={record.id}, cloud_id={cloud_id}")
        else:
            logger.error(f"云端数据同步失败: record_id={record.id}")
