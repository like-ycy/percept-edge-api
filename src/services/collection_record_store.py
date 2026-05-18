"""采集记录持久化与任务进度更新"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import CollectionRecord, Task, now_shanghai
from src.schemas.collection import CollectionRecordStatusEnum


class CollectionRecordStore:
    """采集记录持久化服务"""

    async def create_collecting_record(
        self,
        *,
        db: AsyncSession,
        task_id: int,
        user_id: int,
        user_name: str | None,
        start_time: datetime,
        output_dir: Path,
    ) -> CollectionRecord:
        record = CollectionRecord(
            task_id=task_id,
            user_id=user_id,
            user_name=user_name,
            start_time=start_time,
            output_dir=str(output_dir),
            collection_status=CollectionRecordStatusEnum.COLLECTING.value,
            upload_status="pending",
            materialize_progress=0,
            raw_bytes=0,
            raw_frame_count=0,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        logger.info("采集记录已创建: record_id={}, output_dir={}", record.id, output_dir)
        return record

    async def mark_record_finalizing(
        self,
        *,
        db: AsyncSession,
        record: CollectionRecord,
        end_time: datetime,
        raw_capture_dir: Path,
        raw_bytes: int,
        raw_frame_count: int,
        spool_sealed_at: datetime,
    ) -> CollectionRecord:
        record.end_time = end_time
        record.raw_capture_dir = str(raw_capture_dir)
        record.raw_bytes = raw_bytes
        record.raw_frame_count = raw_frame_count
        record.spool_sealed_at = spool_sealed_at
        record.collection_status = CollectionRecordStatusEnum.FINALIZING.value
        record.materialize_progress = 0
        record.materialize_error = None
        await db.commit()
        await db.refresh(record)
        return record

    async def mark_record_aborted(
        self,
        *,
        db: AsyncSession,
        record: CollectionRecord,
        end_time: datetime | None = None,
    ) -> CollectionRecord:
        record.collection_status = CollectionRecordStatusEnum.ABORTED.value
        if end_time is not None:
            record.end_time = end_time
        await db.commit()
        await db.refresh(record)
        return record

    async def update_materialization_progress(
        self,
        *,
        db: AsyncSession,
        record: CollectionRecord,
        progress: int,
    ) -> CollectionRecord:
        stmt = (
            update(CollectionRecord)
            .where(
                CollectionRecord.id == record.id,
                CollectionRecord.collection_status.in_(
                    {
                        CollectionRecordStatusEnum.FINALIZING.value,
                        CollectionRecordStatusEnum.FINALIZE_FAILED.value,
                    }
                ),
            )
            .values(materialize_progress=max(0, min(progress, 100)))
        )
        await db.execute(stmt)
        await db.commit()
        await db.refresh(record)
        return record

    async def reset_materialization_for_retry(
        self,
        *,
        db: AsyncSession,
        record: CollectionRecord,
    ) -> CollectionRecord:
        record.collection_status = CollectionRecordStatusEnum.FINALIZING.value
        record.materialize_progress = 0
        record.materialize_error = None
        await db.commit()
        await db.refresh(record)
        return record

    async def mark_materialization_succeeded(
        self,
        *,
        db: AsyncSession,
        record: CollectionRecord,
        frame_count: int,
        duration: int,
        file_size: int,
        files: list[str],
    ) -> CollectionRecord:
        record.frame_count = frame_count
        record.duration = duration
        record.file_size = file_size
        record.files = json.dumps(files, ensure_ascii=False)
        record.collection_status = CollectionRecordStatusEnum.VALIDATING.value
        record.materialize_progress = 100
        record.materialize_error = None
        record.materialized_at = now_shanghai()
        await db.commit()
        await db.refresh(record)
        return record

    async def mark_materialization_failed(
        self,
        *,
        db: AsyncSession,
        record: CollectionRecord,
        error_message: str,
    ) -> CollectionRecord:
        record.collection_status = CollectionRecordStatusEnum.FINALIZE_FAILED.value
        record.materialize_error = error_message
        await db.commit()
        await db.refresh(record)
        logger.error("采集记录整理失败: record_id={}, error={}", record.id, error_message)
        return record

    async def delete_record(self, *, db: AsyncSession, record: CollectionRecord) -> None:
        """删除采集记录。"""
        await db.delete(record)
        await db.commit()

    async def update_task_progress(self, db: AsyncSession, task_id: int) -> None:
        """更新任务进度"""
        result = await db.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalars().first()
        if not task:
            return

        task.progress += 1
        logger.info("任务进度更新: task_id={}, progress={}/{}", task_id, task.progress, task.repeat)

        if 0 < task.progress < task.repeat:
            if task.status not in ("stop", "paused"):
                task.status = "run"
                logger.info("任务开始采集: task_id={}", task_id)
            else:
                logger.info("任务已暂停，保持状态: task_id={}, status={}", task_id, task.status)
        elif task.progress == task.repeat:
            task.status = "completed"
            logger.info("任务采集完成: task_id={}", task_id)

        await db.commit()
