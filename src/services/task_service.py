# src/services/task_service.py
"""任务业务逻辑层"""

import asyncio

from loguru import logger

from src.core.sync_throttle import SyncThrottle
from src.models.database import Task
from src.repositories.task_repo import TaskRepository
from src.schemas.status import TaskStatus
from src.schemas.task import SyncResult, TaskFilter, TaskFilterOptions
from src.services.cloud_client import CloudClient
from src.services.task_converter import cloud_detail_to_model
from src.services.task_sync_fetcher import TaskSyncFetcher


class TaskService:
    """任务服务"""

    def __init__(
        self,
        repo: TaskRepository,
        cloud_client: CloudClient,
        throttle: SyncThrottle,
        fetcher: TaskSyncFetcher | None = None,
    ):
        self.repo = repo
        self.cloud_client = cloud_client
        self.throttle = throttle
        self.fetcher = fetcher or TaskSyncFetcher(cloud_client)
        # 同步锁：防止同一用户的并发同步请求
        self._sync_locks: dict[int, asyncio.Lock] = {}

    def _get_sync_lock(self, user_id: int) -> asyncio.Lock:
        """获取用户的同步锁（懒加载）"""
        if user_id not in self._sync_locks:
            self._sync_locks[user_id] = asyncio.Lock()
        return self._sync_locks[user_id]

    async def get_tasks(self, user_id: int, token: str, username: str) -> list[Task]:
        """获取任务列表（仅查询本地）"""
        return await self.repo.list_by_user(user_id)

    async def get_tasks_paginated(
        self,
        user_id: int,
        token: str,
        username: str,
        filters: TaskFilter | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Task], int]:
        """获取任务列表（带过滤和分页，仅查询本地）

        Args:
            user_id: 用户 ID
            token: 认证令牌
            username: 用户名
            filters: 过滤条件
            page: 页码
            page_size: 每页数量

        Returns:
            (任务列表, 总记录数)
        """
        return await self.repo.list_by_user_with_filter(user_id, filters, page, page_size)

    async def get_task(self, task_id: int) -> Task | None:
        """获取单个任务"""
        return await self.repo.get_by_id(task_id)

    async def get_filter_options(
        self, user_id: int, token: str, username: str
    ) -> TaskFilterOptions:
        """获取任务过滤选项（仅查询本地）

        Args:
            user_id: 用户 ID
            token: 认证令牌
            username: 用户名

        Returns:
            TaskFilterOptions 包含各字段的可选值列表
        """
        return await self.repo.get_filter_options(user_id)

    async def force_sync(self, user_id: int, token: str, username: str) -> SyncResult:
        """兼容旧接口，实际执行云端全量同步。"""
        return await self.force_sync_all(token)

    async def force_sync_all(self, token: str) -> SyncResult:
        """主动同步云端全部任务。"""
        lock = self._get_sync_lock(0)
        async with lock:
            result = await self._sync_from_cloud(token)
            self.throttle.mark_synced(0)
            return result

    async def _sync_from_cloud(self, token: str) -> SyncResult:
        """从云端同步任务

        流程：
        1. 获取所有模板
        2. 遍历每个模板，获取其下的任务列表
        3. 对每个任务调用详情接口获取完整数据
        4. 存入本地数据库（按 collector.id 归属）
        5. 删除云端已不存在的本地任务
        """
        added = 0
        updated = 0
        cloud_task_ids: set[int] = set()

        details, cloud_task_ids = await self.fetcher.fetch_task_details(token)

        for detail in details:
            model_data = cloud_detail_to_model(detail)
            if (
                model_data.get("progress") == 0
                and model_data.get("status") == TaskStatus.RUNNING.value
            ):
                model_data["status"] = TaskStatus.PENDING.value

            existing = await self.repo.get_by_cloud_task_id(detail.id, include_deleted=True)
            if existing is not None:
                model_data["status"] = self._resolve_synced_status(
                    local_progress=existing.progress,
                    repeat=existing.repeat or detail.repeat,
                    cloud_status=str(model_data.get("status") or existing.status),
                )

            _, is_new = await self.repo.upsert_by_cloud_task_id(detail.id, model_data)
            if is_new:
                added += 1
            else:
                updated += 1

        # 删除云端已不存在的本地任务
        deleted = await self.repo.delete_excluding_task_ids(cloud_task_ids)
        if deleted:
            logger.info(f"删除本地多余任务: deleted={deleted}")

        total = added + updated
        return SyncResult(added=added, updated=updated, deleted=deleted, total=total)

    @staticmethod
    def _resolve_synced_status(local_progress: int, repeat: int, cloud_status: str) -> str:
        """合并云端状态与本地采集进度，避免已采集任务被刷回 pending。"""
        if repeat > 0 and local_progress >= repeat:
            return TaskStatus.COMPLETED.value
        if local_progress > 0 and cloud_status in {
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
        }:
            return TaskStatus.RUNNING.value
        return cloud_status
