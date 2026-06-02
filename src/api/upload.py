# src/api/upload.py
"""上传 API 路由"""

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.context import get_current_token
from src.core.exceptions import BusinessError, NotFoundError
from src.dependencies import get_current_user, get_db, get_db_session_factory, get_upload_service
from src.models.database import CollectionRecord
from src.schemas.auth import UserInfo
from src.schemas.response import EmptyData, ResponseSchema
from src.schemas.status import CloudNotifyStatus, CollectionRecordStatus, ValidationStatus
from src.schemas.upload import (
    ActiveUploadResponse,
    BatchUploadRequest,
    UploadProgress,
    UploadRequest,
    UploadResult,
    UploadStatus,
)
from src.services.upload_service import UploadService

router = APIRouter()


class BatchUploadData(BaseModel):
    """批量上传响应数据"""

    message: str


@router.post("/start", response_model=ResponseSchema[UploadProgress])
async def start_upload(
    request: UploadRequest,
    user: UserInfo = Depends(get_current_user),
    service: UploadService = Depends(get_upload_service),
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_db_session_factory),
):
    """开始上传单条记录"""
    record = await service.get_record_by_id_for_user(request.record_id, user.user_id, db)
    if not record:
        raise NotFoundError("记录", str(request.record_id))

    if record.upload_status == UploadStatus.COMPLETED.value:
        if record.cloud_id is None:
            service.validate_record_ready_for_cloud_notification(record)
            service.schedule_cloud_notification_retry(
                record,
                session_factory,
                token=get_current_token(),
            )
        return ResponseSchema(
            data=UploadProgress(
                record_id=request.record_id,
                status=UploadStatus.COMPLETED,
                progress=100,
            )
        )

    service.resolve_record_upload_inputs(record)

    if not service.schedule_upload_single(request.record_id, session_factory):
        raise BusinessError("上传任务调度失败，请稍后重试")
    return ResponseSchema(
        data=UploadProgress(record_id=request.record_id, status=UploadStatus.UPLOADING)
    )


@router.post("/batch", response_model=ResponseSchema[BatchUploadData])
async def batch_upload(
    request: BatchUploadRequest,
    user: UserInfo = Depends(get_current_user),
    service: UploadService = Depends(get_upload_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_db_session_factory),
):
    """批量上传"""
    async with session_factory() as db:
        result = await db.execute(
            select(CollectionRecord).where(
                CollectionRecord.id.in_(request.record_ids),
                CollectionRecord.user_id == user.user_id,
            )
        )
        records = {record.id: record for record in result.scalars().all()}

    pending_record_ids: list[int] = []
    notify_retry_records: list[CollectionRecord] = []
    for record_id in request.record_ids:
        record = records.get(record_id)
        if record is None:
            continue
        if record.upload_status == UploadStatus.COMPLETED.value:
            if record.cloud_id is None:
                service.validate_record_ready_for_cloud_notification(record)
                notify_retry_records.append(record)
            continue
        service.resolve_record_upload_inputs(record)
        pending_record_ids.append(record_id)

    for record in notify_retry_records:
        service.schedule_cloud_notification_retry(
            record,
            session_factory,
            token=get_current_token(),
        )

    if pending_record_ids and not service.schedule_upload_batch(
        pending_record_ids, session_factory
    ):
        raise BusinessError("批量上传任务调度失败，请稍后重试")

    return ResponseSchema(
        data=BatchUploadData(
            message=(
                f"已开始上传 {len(pending_record_ids)} 条记录"
                if pending_record_ids
                else "所选记录均已上传完成，未重复发起 rsync"
            )
        )
    )


@router.get("/progress/{record_id}", response_model=ResponseSchema[UploadProgress | EmptyData])
async def get_progress(
    record_id: int,
    user: UserInfo = Depends(get_current_user),
    service: UploadService = Depends(get_upload_service),
    db: AsyncSession = Depends(get_db),
):
    """查询上传进度"""
    progress = await service.get_progress_for_user(record_id, user.user_id, db)
    if progress:
        return ResponseSchema(data=progress)
    return ResponseSchema(data=EmptyData())


@router.get("/active", response_model=ResponseSchema[ActiveUploadResponse])
async def get_active_uploads(
    user: UserInfo = Depends(get_current_user),
    service: UploadService = Depends(get_upload_service),
    db: AsyncSession = Depends(get_db),
):
    """查询当前活跃的上传任务"""
    active = await service.get_active_uploads(db, user_id=user.user_id)
    return ResponseSchema(
        data=ActiveUploadResponse(
            has_active_upload=len(active) > 0,
            records=active,
        )
    )


@router.post("/retry-cloud-notify/{record_id}", response_model=ResponseSchema[UploadProgress])
async def retry_cloud_notify(
    record_id: int,
    user: UserInfo = Depends(get_current_user),
    service: UploadService = Depends(get_upload_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_db_session_factory),
):
    """重新通知云端（仅用于已上传成功但未登记云端的记录）。"""
    async with session_factory() as db:
        record = await service.get_record_by_id_for_user(record_id, user.user_id, db)
        if not record:
            raise NotFoundError("记录", str(record_id))

        if record.cloud_id is not None:
            return ResponseSchema(
                data=UploadProgress(
                    record_id=record_id,
                    status=UploadStatus.COMPLETED,
                    progress=100,
                )
            )

        if record.upload_status != UploadStatus.COMPLETED.value:
            raise BusinessError("文件尚未上传成功，无法单独通知云端")
        service.validate_record_ready_for_cloud_notification(record)

        service.schedule_cloud_notification_retry(
            record, session_factory, token=get_current_token()
        )

    return ResponseSchema(data=UploadProgress(record_id=record_id, status=UploadStatus.UPLOADING))


@router.post("/reconcile-cloud-notify", response_model=ResponseSchema[UploadResult])
async def reconcile_cloud_notify(
    user: UserInfo = Depends(get_current_user),
    service: UploadService = Depends(get_upload_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_db_session_factory),
):
    """批量补偿所有已上传但未通知云端的记录。"""
    async with session_factory() as db:
        result = await db.execute(
            select(CollectionRecord).where(
                CollectionRecord.upload_status == UploadStatus.COMPLETED.value,
                CollectionRecord.collection_status == CollectionRecordStatus.COMPLETED.value,
                CollectionRecord.validation_status == ValidationStatus.SUCCESS.value,
                CollectionRecord.cloud_id.is_(None),
                or_(
                    CollectionRecord.cloud_notify_status.is_(None),
                    CollectionRecord.cloud_notify_status != CloudNotifyStatus.NOTIFYING.value,
                ),
                CollectionRecord.user_id == user.user_id,
                CollectionRecord.task_id.isnot(None),
                CollectionRecord.output_dir.isnot(None),
                CollectionRecord.files.isnot(None),
            )
        )
        records = result.scalars().all()

    success, failed = [], []
    for record in records:
        try:
            service.validate_record_ready_for_cloud_notification(record)
            service.schedule_cloud_notification_retry(
                record, session_factory, token=get_current_token()
            )
            success.append(record.id)
        except Exception as e:
            logger.error(f"补偿调度失败: record_id={record.id}, error={e}")
            failed.append(record.id)

    return ResponseSchema(data=UploadResult(success=success, failed=failed))
