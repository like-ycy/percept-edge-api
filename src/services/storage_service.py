"""存储业务逻辑层"""

import json
import shutil
from pathlib import Path

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import BusinessError, NotFoundError
from src.models.database import CollectionRecord, Task
from src.schemas.collection import CollectionRecordStatusEnum
from src.schemas.storage import (
    CollectionRecordCreate,
    CollectionRecordFilter,
    CollectionRecordResponse,
    StorageFilterOptions,
)


def record_to_response(
    record: CollectionRecord, task: Task | None = None
) -> CollectionRecordResponse:
    """将数据库记录转换为响应 Schema"""
    files = []
    if record.files:
        try:
            files = json.loads(record.files)
        except (json.JSONDecodeError, TypeError):
            pass

    return CollectionRecordResponse(
        id=record.id,
        cloud_id=record.cloud_id,
        task_id=record.task_id,
        user_name=record.user_name,
        file_size=record.file_size,
        duration=record.duration,
        start_time=record.start_time,
        end_time=record.end_time,
        collection_status=record.collection_status,
        validation_status=record.validation_status,
        validation_summary=record.validation_summary,
        upload_status=record.upload_status,
        upload_progress=record.upload_progress,
        materialize_progress=record.materialize_progress,
        materialize_error=record.materialize_error,
        raw_bytes=record.raw_bytes,
        raw_frame_count=record.raw_frame_count,
        output_dir=record.output_dir,
        raw_capture_dir=record.raw_capture_dir,
        files=files,
        template_name=task.template_name if task else None,
        purpose_name=task.purpose_name if task else None,
        device_type_name=task.device_type_name if task else None,
        plan_name=task.plan_name if task else None,
    )


class DatabaseStorageService:
    """数据库驱动的存储服务"""

    _PROGRESS_COUNTED_STATUSES = {
        CollectionRecordStatusEnum.FINALIZING.value,
        CollectionRecordStatusEnum.VALIDATING.value,
        CollectionRecordStatusEnum.COMPLETED.value,
        CollectionRecordStatusEnum.FINALIZE_FAILED.value,
        CollectionRecordStatusEnum.VALIDATION_FAILED.value,
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_record_by_id(self, record_id: int, user_id: int) -> CollectionRecord:
        query = select(CollectionRecord).where(
            CollectionRecord.id == record_id,
            CollectionRecord.user_id == user_id,
        )
        result = await self.db.execute(query)
        record = result.scalar_one_or_none()
        if record is None:
            raise NotFoundError("采集记录", str(record_id))
        return record

    async def delete_record(self, record_id: int, user_id: int) -> CollectionRecord:
        record = await self.get_record_by_id(record_id, user_id)
        await self.delete_record_instance(record)
        return record

    async def delete_record_instance(self, record: CollectionRecord) -> None:
        await self._rollback_task_progress_for_deleted_record(record)
        await self.db.delete(record)
        await self.db.commit()

    async def _rollback_task_progress_for_deleted_record(self, record: CollectionRecord) -> None:
        if record.task_id is None:
            return
        if record.collection_status not in self._PROGRESS_COUNTED_STATUSES:
            return

        result = await self.db.execute(
            select(Task).where(Task.task_id == record.task_id, Task.is_deleted.is_(False))
        )
        task = result.scalar_one_or_none()
        if task is None:
            return

        task.progress = max(task.progress - 1, 0)
        previous_status = task.status
        if task.progress == 0:
            task.status = "pending"
        elif task.progress < task.repeat:
            if previous_status not in ("stop", "paused"):
                task.status = "run"
        else:
            task.status = "completed"

        logger.info(
            "删除采集记录后回滚任务进度: record_id={}, task_id={}, progress={}/{}, status={}",
            record.id,
            record.task_id,
            task.progress,
            task.repeat,
            task.status,
        )

    @staticmethod
    def remove_local_record_data(record: CollectionRecord) -> None:
        directories: set[Path] = set()

        if record.files:
            try:
                file_list = json.loads(record.files)
                if isinstance(file_list, list):
                    for file_path in file_list:
                        if isinstance(file_path, str) and file_path:
                            directories.add(Path(file_path).parent)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"解析记录 files 失败: record_id={record.id}")

        if not directories and record.output_dir:
            directories.add(Path(record.output_dir))

        for directory in directories:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                logger.info(f"已删除本地采集目录: record_id={record.id}, path={directory}")

    async def create_record(self, data: CollectionRecordCreate) -> CollectionRecordResponse:
        record = CollectionRecord(**data.model_dump())
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record_to_response(record)

    async def get_records(self, task_id: int | None = None) -> list[CollectionRecordResponse]:
        query = select(CollectionRecord).order_by(CollectionRecord.created_at.desc())
        if task_id is not None:
            query = query.where(CollectionRecord.task_id == task_id)
        result = await self.db.execute(query)
        return [record_to_response(r) for r in result.scalars().all()]

    async def get_records_paginated(
        self,
        user_id: int,
        filters: CollectionRecordFilter | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CollectionRecordResponse], int]:
        query = (
            select(CollectionRecord, Task)
            .outerjoin(Task, CollectionRecord.task_id == Task.task_id)
            .where(CollectionRecord.user_id == user_id)
        )
        count_query = (
            select(func.count())
            .select_from(CollectionRecord)
            .outerjoin(Task, CollectionRecord.task_id == Task.task_id)
            .where(CollectionRecord.user_id == user_id)
        )

        if filters:
            if filters.upload_status:
                query = query.where(CollectionRecord.upload_status == filters.upload_status)
                count_query = count_query.where(
                    CollectionRecord.upload_status == filters.upload_status
                )
            if filters.task_id:
                query = query.where(CollectionRecord.task_id == filters.task_id)
                count_query = count_query.where(CollectionRecord.task_id == filters.task_id)
            if filters.user_name:
                query = query.where(CollectionRecord.user_name.ilike(f"%{filters.user_name}%"))
                count_query = count_query.where(
                    CollectionRecord.user_name.ilike(f"%{filters.user_name}%")
                )
            if filters.template_name:
                query = query.where(Task.template_name.ilike(f"%{filters.template_name}%"))
                count_query = count_query.where(
                    Task.template_name.ilike(f"%{filters.template_name}%")
                )
            if filters.device_type_name:
                query = query.where(Task.device_type_name == filters.device_type_name)
                count_query = count_query.where(Task.device_type_name == filters.device_type_name)
            if filters.plan_name:
                query = query.where(Task.plan_name == filters.plan_name)
                count_query = count_query.where(Task.plan_name == filters.plan_name)

        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.order_by(CollectionRecord.created_at.desc()).offset(offset).limit(page_size)
        result = await self.db.execute(query)
        records = [record_to_response(r, t) for r, t in result.all()]

        return records, total

    async def retry_materialization(
        self,
        *,
        record_id: int,
        user_id: int,
        db: AsyncSession,
        collection_service,
    ) -> None:
        record = await self.get_record_by_id(record_id=record_id, user_id=user_id)
        if record.collection_status not in {
            CollectionRecordStatusEnum.FINALIZE_FAILED.value,
            CollectionRecordStatusEnum.FINALIZING.value,
        }:
            raise BusinessError("当前记录状态不支持重试整理")
        if not record.raw_capture_dir or not Path(record.raw_capture_dir).exists():
            raise BusinessError("raw spool 不存在，无法重试整理")

        if record.collection_status == CollectionRecordStatusEnum.FINALIZE_FAILED.value:
            record.collection_status = CollectionRecordStatusEnum.FINALIZING.value
            record.materialize_progress = 0
            record.materialize_error = None
            await db.commit()
            await db.refresh(record)

        scheduled = collection_service.schedule_materialization(record.id)
        if not scheduled:
            raise BusinessError("整理任务调度失败，请稍后重试")

    async def get_filter_options(self, user_id: int) -> StorageFilterOptions:
        async def get_distinct_record_values(column) -> list[str]:
            query = (
                select(column)
                .where(CollectionRecord.user_id == user_id)
                .where(column.isnot(None))
                .where(column != "")
                .distinct()
            )
            result = await self.db.execute(query)
            return [str(v) for v in result.scalars().all()]

        async def get_distinct_task_values(column) -> list[str]:
            query = (
                select(column)
                .select_from(CollectionRecord)
                .join(Task, CollectionRecord.task_id == Task.task_id)
                .where(CollectionRecord.user_id == user_id)
                .where(column.isnot(None))
                .where(column != "")
                .distinct()
            )
            result = await self.db.execute(query)
            return [str(v) for v in result.scalars().all()]

        return StorageFilterOptions(
            upload_status=await get_distinct_record_values(CollectionRecord.upload_status),
            user_name=await get_distinct_record_values(CollectionRecord.user_name),
            template_name=await get_distinct_task_values(Task.template_name),
            device_type_name=await get_distinct_task_values(Task.device_type_name),
            plan_name=await get_distinct_task_values(Task.plan_name),
        )
