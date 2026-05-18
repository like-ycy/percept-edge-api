"""上传记录持久化操作"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import CollectionRecord


class UploadRecordStore:
    """采集记录上传相关持久化操作"""

    async def get_record_by_id(self, record_id: int, db: AsyncSession) -> CollectionRecord | None:
        """根据 ID 获取采集记录"""
        result = await db.execute(select(CollectionRecord).where(CollectionRecord.id == record_id))
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

    async def update_cloud_id(self, record_id: int, cloud_id: int, db: AsyncSession) -> None:
        """更新云端数据 ID。"""
        record = await self.get_record_by_id(record_id, db)
        if record:
            record.cloud_id = cloud_id
            await db.commit()
