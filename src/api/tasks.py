# src/api/tasks.py
"""任务 API 路由"""

import math

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from src.dependencies import get_current_user, get_task_service, get_task_sync_service
from src.schemas.auth import UserInfo
from src.schemas.query import TaskQueryParams
from src.schemas.response import ResponseSchema
from src.schemas.task import PaginatedTaskResponse, SyncResult, TaskFilter, TaskFilterOptions
from src.services.task_converter import task_to_response
from src.services.task_service import TaskService
from src.services.task_sync_service import TaskSyncService

router = APIRouter()


class TaskSyncAcceptedData(BaseModel):
    """任务同步触发响应"""

    message: str


@router.get("", response_model=ResponseSchema[PaginatedTaskResponse])
async def list_tasks(
    authorization: str = Header(...),
    params: TaskQueryParams = Depends(),
    user: UserInfo = Depends(get_current_user),
    service: TaskService = Depends(get_task_service),
    task_sync_service: TaskSyncService = Depends(get_task_sync_service),
):
    """获取任务列表（支持过滤和分页，并注册后台同步上下文）"""
    token = authorization.replace("Bearer ", "")
    task_sync_service.register_user(user.user_id, user.user_name, token)

    # 构建过滤条件（仅在有过滤条件时）
    filters = None
    if params.has_filters():
        filters = TaskFilter(
            status=params.status,
            task_id=params.task_id,
            device_type_name=params.device_type_name,
            plan_name=params.plan_name,
            template_name=params.template_name,
            collector_name=params.collector_name,
            created_user_name=params.created_user_name,
            updated_user_name=params.updated_user_name,
        )

    tasks, total = await service.get_tasks_paginated(
        user.user_id,
        token,
        user.user_name,
        filters,
        params.page,
        params.page_size,
    )

    total_pages = math.ceil(total / params.page_size) if total > 0 else 0

    return ResponseSchema(
        data=PaginatedTaskResponse(
            items=[task_to_response(t) for t in tasks],
            total=total,
            page=params.page,
            page_size=params.page_size,
            total_pages=total_pages,
        )
    )


@router.get("/filters", response_model=ResponseSchema[TaskFilterOptions])
async def get_task_filters(
    user: UserInfo = Depends(get_current_user),
    service: TaskService = Depends(get_task_service),
):
    """获取任务过滤选项

    返回当前用户本地任务中各过滤字段的可选值列表。
    """
    options = await service.get_filter_options(user.user_id, "", user.user_name)
    return ResponseSchema(data=options)


@router.post("/sync", response_model=ResponseSchema[SyncResult | TaskSyncAcceptedData])
async def sync_tasks(
    authorization: str = Header(...),
    user: UserInfo = Depends(get_current_user),
    task_sync_service: TaskSyncService = Depends(get_task_sync_service),
):
    """主动触发一次任务同步，并更新后台同步上下文"""
    token = authorization.replace("Bearer ", "")
    task_sync_service.register_user(user.user_id, user.user_name, token)
    result = await task_sync_service.sync_user(user.user_id, force=True)
    if result is not None:
        return ResponseSchema(data=result)
    return ResponseSchema(data=TaskSyncAcceptedData(message="任务同步已触发"))
