"""上传记录持久化操作"""

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import CollectionRecord
from src.schemas.status import CloudNotifyStatus


class UploadRecordStore:
    """采集记录上传相关持久化操作"""

    async def get_record_by_id(self, record_id: int, db: AsyncSession) -> CollectionRecord | None:
        """根据 ID 获取采集记录"""
        result = await db.execute(select(CollectionRecord).where(CollectionRecord.id == record_id))
        return result.scalar_one_or_none()

    async def get_record_by_id_for_user(
        self, record_id: int, user_id: int, db: AsyncSession
    ) -> CollectionRecord | None:
        """根据 ID 和用户 ID 获取采集记录。"""
        result = await db.execute(
            select(CollectionRecord).where(
                CollectionRecord.id == record_id,
                CollectionRecord.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_upload_status(self, record_id: int, status: str, db: AsyncSession) -> None:
        """更新上传状态"""
        record = await self.get_record_by_id(record_id, db)
        if record:
            record.upload_status = status
            await db.commit()

    async def update_upload_progress(self, record_id: int, progress: int, db: AsyncSession) -> None:
        """更新数据库上传进度"""
        record = await self.get_record_by_id(record_id, db)
        if record:
            record.upload_progress = progress
            await db.commit()

    async def update_upload_window(
        self,
        record_id: int,
        upload_started_at: datetime,
        upload_finished_at: datetime,
        db: AsyncSession,
    ) -> None:
        """更新数据库中的上传起止时间。"""
        record = await self.get_record_by_id(record_id, db)
        if record:
            record.upload_started_at = upload_started_at
            record.upload_finished_at = upload_finished_at
            await db.commit()

    async def update_cloud_id(self, record_id: int, cloud_id: int, db: AsyncSession) -> None:
        """更新云端数据 ID。"""
        record = await self.get_record_by_id(record_id, db)
        if record:
            record.cloud_id = cloud_id
            await db.commit()

    async def update_cloud_notify_status(
        self,
        record_id: int,
        *,
        status: str | None = None,
        error: str | None = None,
        clear_error: bool = False,
        attempts: int | None = None,
        notified_at=None,
        db: AsyncSession,
    ) -> None:
        """更新云端通知相关字段。"""
        record = await self.get_record_by_id(record_id, db)
        if not record:
            return
        if status is not None:
            record.cloud_notify_status = status
        if clear_error:
            record.cloud_notify_error = None
        elif error is not None:
            record.cloud_notify_error = error
        if attempts is not None:
            record.cloud_notify_attempts = attempts
        if notified_at is not None:
            record.cloud_notified_at = notified_at
        await db.commit()

    async def mark_cloud_notify_in_progress(self, record_id: int, db: AsyncSession) -> bool:
        """将云端通知标记为进行中。

        返回 True 表示本次调用成功获得通知执行权；返回 False 表示记录不存在、已通知或已有通知任务进行中。
        """
        result = await db.execute(
            update(CollectionRecord)
            .where(
                CollectionRecord.id == record_id,
                CollectionRecord.cloud_id.is_(None),
                or_(
                    CollectionRecord.cloud_notify_status.is_(None),
                    CollectionRecord.cloud_notify_status != CloudNotifyStatus.NOTIFYING.value,
                ),
            )
            .values(
                cloud_notify_status=CloudNotifyStatus.NOTIFYING.value,
                cloud_notify_attempts=func.coalesce(CollectionRecord.cloud_notify_attempts, 0) + 1,
            )
            .returning(CollectionRecord.id)
        )
        updated_id = result.scalar_one_or_none()
        await db.commit()
        return updated_id is not None
