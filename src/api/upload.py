# src/api/upload.py
"""上传 API 路由"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.exceptions import BusinessError, NotFoundError
from src.dependencies import get_current_user, get_db, get_db_session_factory, get_upload_service
from src.schemas.auth import UserInfo
from src.schemas.response import EmptyData, ResponseSchema
from src.schemas.upload import (
    ActiveUploadResponse,
    BatchUploadRequest,
    UploadProgress,
    UploadRequest,
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
    record = await service.get_record_by_id(request.record_id, db)
    if not record:
        raise NotFoundError("记录", str(request.record_id))

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
    if not service.schedule_upload_batch(request.record_ids, session_factory):
        raise BusinessError("批量上传任务调度失败，请稍后重试")
    return ResponseSchema(
        data=BatchUploadData(message=f"已开始上传 {len(request.record_ids)} 条记录")
    )


@router.get("/progress/{record_id}", response_model=ResponseSchema[UploadProgress | EmptyData])
async def get_progress(
    record_id: int,
    user: UserInfo = Depends(get_current_user),
    service: UploadService = Depends(get_upload_service),
):
    """查询上传进度"""
    progress = service.get_progress(record_id)
    if progress:
        return ResponseSchema(data=progress)
    return ResponseSchema(data=EmptyData())


@router.get("/active", response_model=ResponseSchema[ActiveUploadResponse])
async def get_active_uploads(
    service: UploadService = Depends(get_upload_service),
    db: AsyncSession = Depends(get_db),
):
    """查询当前活跃的上传任务"""
    active = await service.get_active_uploads(db)
    return ResponseSchema(
        data=ActiveUploadResponse(
            has_active_upload=len(active) > 0,
            records=active,
        )
    )
