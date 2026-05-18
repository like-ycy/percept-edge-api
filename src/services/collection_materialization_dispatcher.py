"""采集 raw spool 后台整理调度器"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from src.services.collection_materializer import CollectionMaterializer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.core.task_manager import BackgroundTaskManager
    from src.services.collection_service import CollectionService


class CollectionMaterializationDispatcher:
    """后台整理调度器"""

    def __init__(
        self,
        *,
        task_manager: "BackgroundTaskManager | None",
        session_maker: "async_sessionmaker | None",
        collection_service: "CollectionService",
    ) -> None:
        self._task_manager = task_manager
        self._session_maker = session_maker
        self._collection_service = collection_service
        self._inflight_record_ids: set[int] = set()
        self._fallback_tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(1)

    def schedule(
        self,
        record_id: int,
        *,
        validation_upload_after_success: bool = True,
    ) -> bool:
        if record_id in self._inflight_record_ids:
            logger.warning("采集整理任务已在执行中，跳过重复调度: record_id={}", record_id)
            return False
        if self._session_maker is None:
            logger.error("未配置数据库会话工厂，无法调度采集整理: record_id={}", record_id)
            return False

        self._inflight_record_ids.add(record_id)
        task_id = f"collection_materialize_{record_id}_{datetime.now().timestamp()}"
        coro = self._materialize_and_continue(
            record_id,
            validation_upload_after_success=validation_upload_after_success,
        )
        if self._task_manager:
            task = self._task_manager.create_task(task_id=task_id, coro=coro, critical=True)
            if task is None:
                self._inflight_record_ids.discard(record_id)
                return False
            return True

        task = asyncio.create_task(coro)
        self._fallback_tasks.add(task)
        task.add_done_callback(self._fallback_tasks.discard)
        return True

    async def _materialize_and_continue(
        self,
        record_id: int,
        *,
        validation_upload_after_success: bool,
    ) -> None:
        try:
            while self._collection_service.is_collecting:
                await asyncio.sleep(0.5)

            async with self._semaphore:
                assert self._session_maker is not None
                materializer = CollectionMaterializer(session_maker=self._session_maker)
                succeeded = await materializer.materialize(record_id)
                if succeeded:
                    scheduled = self._collection_service.schedule_validation(
                        record_id,
                        upload_after_success=validation_upload_after_success,
                    )
                    if not scheduled:
                        logger.warning("采集记录校验调度失败: record_id={}", record_id)
        finally:
            self._inflight_record_ids.discard(record_id)
