"""存储 API 路由"""

import math
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_context import get_app_context
from src.core.exceptions import BusinessError, ExternalServiceError, NotFoundError
from src.dependencies import (
    get_cloud_client,
    get_collection_service,
    get_current_user,
    get_database_storage_service,
    get_db,
)
from src.models.database import CollectionRecord
from src.schemas.auth import UserInfo
from src.schemas.collection import RawCaptureInfo
from src.schemas.query import StorageQueryParams
from src.schemas.response import ResponseSchema
from src.schemas.storage import (
    CollectionRecordFilter,
    PaginatedCollectionRecordResponse,
    StorageFilterOptions,
)
from src.services.cloud_client import CloudClient
from src.services.collection_service import CollectionService
from src.services.raw_capture_inspector import inspect_raw_capture
from src.services.storage_service import DatabaseStorageService

router = APIRouter()


@router.get("/files", response_model=ResponseSchema[PaginatedCollectionRecordResponse])
async def list_files(
    authorization: str = Header(...),
    params: StorageQueryParams = Depends(),
    user: UserInfo = Depends(get_current_user),
    service: DatabaseStorageService = Depends(get_database_storage_service),
):
    """查询采集记录（支持过滤和分页）

    默认使用用户ID过滤，其他过滤条件在用户过滤基础上叠加。
    """
    filters = None
    if params.has_filters():
        filters = CollectionRecordFilter(
            upload_status=params.upload_status,
            task_id=params.task_id,
            user_name=params.user_name,
            template_name=params.template_name,
            device_type_name=params.device_type_name,
            plan_name=params.plan_name,
        )

    records, total = await service.get_records_paginated(
        user.user_id,
        filters,
        params.page,
        params.page_size,
    )

    total_pages = math.ceil(total / params.page_size) if total > 0 else 0

    return ResponseSchema(
        data=PaginatedCollectionRecordResponse(
            items=records,
            total=total,
            page=params.page,
            page_size=params.page_size,
            total_pages=total_pages,
        )
    )


@router.get("/files/filters", response_model=ResponseSchema[StorageFilterOptions])
async def get_storage_filters(
    authorization: str = Header(...),
    user: UserInfo = Depends(get_current_user),
    service: DatabaseStorageService = Depends(get_database_storage_service),
):
    """获取存储过滤选项"""
    options = await service.get_filter_options(user.user_id)
    return ResponseSchema(data=options)


@router.post("/files/{record_id}/retry-materialize", response_model=ResponseSchema[dict])
async def retry_materialize(
    record_id: int,
    user: UserInfo = Depends(get_current_user),
    service: DatabaseStorageService = Depends(get_database_storage_service),
    collection_service: CollectionService = Depends(get_collection_service),
    db: AsyncSession = Depends(get_db),
):
    """重试后台整理。"""
    await service.retry_materialization(
        record_id=record_id,
        user_id=user.user_id,
        db=db,
        collection_service=collection_service,
    )
    return ResponseSchema(data={})


@router.post("/files/{record_id}/delete", response_model=ResponseSchema[dict])
async def delete_file_record(
    record_id: int,
    user: UserInfo = Depends(get_current_user),
    service: DatabaseStorageService = Depends(get_database_storage_service),
    cloud_client: CloudClient = Depends(get_cloud_client),
):
    """删除采集记录。"""
    record = await service.get_record_by_id(record_id=record_id, user_id=user.user_id)

    if record.upload_status == "uploading":
        raise BusinessError("数据正在上传中，暂不可以删除")

    if record.cloud_id is None:
        raise BusinessError("数据尚未上传完成，缺少 cloud_id，暂不可以删除")

    deleted = await cloud_client.delete_data(record.cloud_id)
    if not deleted:
        raise ExternalServiceError("CloudAPI", f"删除云端记录失败: cloud_id={record.cloud_id}")

    service.remove_local_record_data(record)

    await service.delete_record_instance(record)

    return ResponseSchema(data={})


@router.get("/raw-info", response_model=ResponseSchema[RawCaptureInfo])
async def get_raw_capture_info(
    record_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """查看采集记录的原始 bin 数据概要（摄像头数 / 帧数）

    仅当 .capture 未被清理时可用（通常在校验失败后保留）。
    """
    record = await db.get(CollectionRecord, record_id)
    if record is None:
        raise NotFoundError("采集记录", str(record_id))

    context = get_app_context(request.app)
    storage_root = Path(context.settings.storage.base_path).resolve()

    raw_raw: str | None = record.raw_capture_dir
    if not raw_raw and record.output_dir:
        raw_raw = str(Path(record.output_dir) / ".capture")

    if not raw_raw:
        raise BusinessError("该记录无原始采集目录信息")

    if ".." in Path(raw_raw).parts:
        raise BusinessError("原始目录路径非法")

    raw_dir = Path(raw_raw).resolve()

    try:
        common = Path(os.path.commonpath([str(raw_dir), str(storage_root)]))
    except ValueError as exc:
        raise BusinessError("原始目录不在存储根路径内") from exc
    if common != storage_root:
        raise BusinessError("原始目录不在存储根路径内")

    if not raw_dir.exists():
        raise BusinessError("原始采集目录不存在（可能已因校验通过被清理）")
    if not (raw_dir / "manifest.json").exists():
        raise BusinessError("原始采集目录缺少 manifest.json")

    info = await inspect_raw_capture(
        record_id=record_id,
        output_dir=record.output_dir,
        capture_dir=raw_dir,
    )
    return ResponseSchema(data=info)
