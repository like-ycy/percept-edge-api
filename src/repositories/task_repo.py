# src/repositories/task_repo.py
"""任务数据访问层"""

import time
from typing import Sequence, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.database import Task
from src.schemas.task import TaskFilter, TaskFilterOptions


class TaskRepository:
    """任务 Repository

    设计说明：
    - 查询方法支持 `load_relations` 参数，用于预加载关联关系（避免 N+1 问题）
    - 更新方法提供两种模式：
      - `update`: 先查询再更新，适合需要返回完整对象的场景
      - `update_status_*`: 直接 UPDATE 语句，适合只更新状态的高效场景
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _active_task_filter():
        return Task.is_deleted.is_(False)

    async def get_by_id(
        self, task_id: int, *, load_relations: Sequence[str] | None = None
    ) -> Task | None:
        """根据本地 ID 获取任务

        Args:
            task_id: 本地任务 ID
            load_relations: 需要预加载的关联关系名称列表（如 ["collection_records"]）
        """
        if load_relations:
            # 使用 select + options 进行关联加载
            query = select(Task).where(Task.id == task_id, self._active_task_filter())
            for relation in load_relations:
                query = query.options(selectinload(getattr(Task, relation)))
            result = await self.session.execute(query)
            return result.scalar_one_or_none()
        result = await self.session.execute(
            select(Task).where(Task.id == task_id, self._active_task_filter())
        )
        return result.scalar_one_or_none()

    async def get_by_task_id(
        self, user_id: int, task_id: int, *, include_deleted: bool = False
    ) -> Task | None:
        """根据用户 ID 和云端任务 ID 获取任务

        Args:
            user_id: 用户 ID
            task_id: 云端任务 ID

        Returns:
            匹配的任务，如果不存在则返回 None
        """
        result = await self.session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.task_id == task_id,
                *([] if include_deleted else [self._active_task_filter()]),
            )
        )
        return result.scalars().first()

    async def get_by_cloud_task_id(
        self, task_id: int, *, include_deleted: bool = False
    ) -> Task | None:
        """根据云端任务 ID 获取任务。"""
        result = await self.session.execute(
            select(Task).where(
                Task.task_id == task_id,
                *([] if include_deleted else [self._active_task_filter()]),
            )
        )
        return result.scalars().first()

    async def list_all(self) -> list[Task]:
        """获取所有任务"""
        result = await self.session.execute(
            select(Task).where(self._active_task_filter()).order_by(Task.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_user(self, user_id: int) -> list[Task]:
        """获取用户的任务列表"""
        result = await self.session.execute(
            select(Task)
            .where(Task.user_id == user_id, self._active_task_filter())
            .order_by(Task.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_user_with_filter(
        self,
        user_id: int,
        filters: TaskFilter | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Task], int]:
        """获取用户的任务列表（带过滤和分页）

        Args:
            user_id: 用户 ID
            filters: 过滤条件
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            (任务列表, 总记录数)
        """
        # 构建基础查询
        query = select(Task).where(Task.user_id == user_id, self._active_task_filter())

        # 应用过滤条件
        if filters:
            if filters.status:
                query = query.where(Task.status == filters.status)
            if filters.task_id:
                query = query.where(Task.task_id == filters.task_id)
            if filters.device_type_name:
                query = query.where(Task.device_type_name == filters.device_type_name)
            if filters.plan_name:
                query = query.where(Task.plan_name == filters.plan_name)
            if filters.template_name:
                query = query.where(Task.template_name == filters.template_name)
            if filters.collector_name:
                query = query.where(Task.collector_name == filters.collector_name)
            if filters.created_user_name:
                query = query.where(Task.task_created_user_name == filters.created_user_name)
            if filters.updated_user_name:
                query = query.where(Task.task_updated_user_name == filters.updated_user_name)

        # 查询总数
        count_query = query.with_only_columns(func.count()).order_by(None)
        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        # 分页查询
        offset = (page - 1) * page_size
        query = query.order_by(Task.created_at.desc()).offset(offset).limit(page_size)
        result = await self.session.execute(query)
        tasks = list(result.scalars().all())

        return tasks, total

    async def create(self, data: dict) -> Task:
        """创建任务"""
        task = Task(**data)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def upsert(self, user_id: int, task_id: int, data: dict) -> tuple[Task, bool]:
        """插入或更新任务（原子操作，避免竞态条件）

        Args:
            user_id: 用户 ID
            task_id: 云端任务 ID
            data: 任务数据

        Returns:
            (任务对象, 是否为新创建) - True 表示新创建，False 表示更新
        """
        # 先尝试查询
        existing = await self.get_by_task_id(user_id, task_id, include_deleted=True)

        if existing:
            # 更新现有任务
            for key, value in data.items():
                if key not in ("user_id", "progress"):  # 不更新这些字段
                    setattr(existing, key, value)
            existing.is_deleted = False
            await self.session.commit()
            await self.session.refresh(existing)
            return existing, False
        else:
            # 创建新任务
            try:
                task = Task(**data)
                self.session.add(task)
                await self.session.commit()
                await self.session.refresh(task)
                return task, True
            except IntegrityError:
                # 如果插入失败（唯一约束冲突），回滚并重新查询
                await self.session.rollback()
                existing = await self.get_by_task_id(user_id, task_id, include_deleted=True)
                if existing:
                    # 找到了，说明是并发插入，返回已存在的记录
                    return existing, False
                else:
                    # 还是没找到，说明是其他错误，重新抛出
                    raise

    async def upsert_by_cloud_task_id(self, task_id: int, data: dict) -> tuple[Task, bool]:
        """按云端 task_id 插入或更新任务。"""
        existing = await self.get_by_cloud_task_id(task_id, include_deleted=True)

        if existing:
            for key, value in data.items():
                if key != "progress":
                    setattr(existing, key, value)
            existing.is_deleted = False
            await self.session.commit()
            await self.session.refresh(existing)
            return existing, False

        try:
            task = Task(**data)
            self.session.add(task)
            await self.session.commit()
            await self.session.refresh(task)
            return task, True
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_cloud_task_id(task_id, include_deleted=True)
            if existing:
                return existing, False
            raise

    async def update(self, task_id: int, data: dict) -> Task | None:
        """更新任务（先查询再更新，返回完整对象）

        适用场景：需要返回更新后的完整 Task 对象
        """
        task = await self.get_by_id(task_id)
        if task:
            for key, value in data.items():
                setattr(task, key, value)
            await self.session.commit()
            await self.session.refresh(task)
        return task

    async def update_status_by_id(self, task_id: int, status: str) -> int:
        """高效更新任务状态（直接 UPDATE 语句）

        适用场景：只需更新状态，不需要返回完整对象

        Args:
            task_id: 本地任务 ID
            status: 新状态

        Returns:
            受影响的行数 (0 表示任务不存在）
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id, self._active_task_filter())
            .values(status=status, updated_at=int(time.time()))
        )
        result = await self.session.execute(stmt)
        cursor_result: CursorResult = cast(CursorResult, result)
        await self.session.commit()
        return cursor_result.rowcount

    async def update_local_status(self, task_id: int, status: str) -> int:
        """高效更新本地执行状态（直接 UPDATE 语句）

        Args:
            task_id: 本地任务 ID
            status: 新本地状态

        Returns:
            受影响的行数 (0 表示任务不存在）
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id, self._active_task_filter())
            .values(local_status=status, updated_at=int(time.time()))
        )
        result = await self.session.execute(stmt)
        cursor_result: CursorResult = cast(CursorResult, result)
        await self.session.commit()
        return cursor_result.rowcount

    async def update_progress(self, task_id: int, progress: int) -> int:
        """高效更新任务进度（直接 UPDATE 语句）

        Args:
            task_id: 本地任务 ID
            progress: 新进度值

        Returns:
            受影响的行数 (0 表示任务不存在）
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id, self._active_task_filter())
            .values(progress=progress, updated_at=int(time.time()))
        )
        result = await self.session.execute(stmt)
        cursor_result: CursorResult = cast(CursorResult, result)
        await self.session.commit()
        return cursor_result.rowcount

    async def increment_progress(self, task_id: int) -> int:
        """原子递增任务进度

        Args:
            task_id: 本地任务 ID

        Returns:
            受影响的行数
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id, self._active_task_filter())
            .values(progress=Task.progress + 1, updated_at=int(time.time()))
        )
        result = await self.session.execute(stmt)
        cursor_result: CursorResult = cast(CursorResult, result)
        await self.session.commit()
        return cursor_result.rowcount

    async def delete_by_user_excluding_task_ids(self, user_id: int, keep_task_ids: set[int]) -> int:
        """删除用户下不在 keep_task_ids 中的任务

        Args:
            user_id: 用户 ID
            keep_task_ids: 需要保留的云端 task_id 集合

        Returns:
            删除的行数
        """
        stmt = (
            update(Task)
            .where(Task.user_id == user_id, self._active_task_filter())
            .values(is_deleted=True, updated_at=int(time.time()))
        )
        if keep_task_ids:
            stmt = stmt.where(Task.task_id.notin_(keep_task_ids))
        result = await self.session.execute(stmt)
        cursor_result: CursorResult = cast(CursorResult, result)
        await self.session.commit()
        return cursor_result.rowcount

    async def delete_excluding_task_ids(self, keep_task_ids: set[int]) -> int:
        """删除本地下不在 keep_task_ids 中的所有任务。"""
        stmt = (
            update(Task)
            .where(self._active_task_filter())
            .values(is_deleted=True, updated_at=int(time.time()))
        )
        if keep_task_ids:
            stmt = stmt.where(Task.task_id.notin_(keep_task_ids))
        result = await self.session.execute(stmt)
        cursor_result: CursorResult = cast(CursorResult, result)
        await self.session.commit()
        return cursor_result.rowcount

    async def get_filter_options(self, user_id: int) -> TaskFilterOptions:
        """获取用户任务的所有可过滤字段的唯一值

        使用 DISTINCT 查询高效获取每个字段的唯一值。

        Args:
            user_id: 用户 ID

        Returns:
            TaskFilterOptions 包含各字段的可选值列表
        """

        async def get_distinct_values(column) -> list[str]:
            """获取指定列的非空唯一值"""
            query = (
                select(column)
                .where(Task.user_id == user_id)
                .where(Task.is_deleted.is_(False))
                .where(column.isnot(None))
                .where(column != "")
                .distinct()
            )
            result = await self.session.execute(query)
            return [str(v) for v in result.scalars().all()]

        return TaskFilterOptions(
            status=await get_distinct_values(Task.status),
            device_type_name=await get_distinct_values(Task.device_type_name),
            plan_name=await get_distinct_values(Task.plan_name),
            template_name=await get_distinct_values(Task.template_name),
            collector_name=await get_distinct_values(Task.collector_name),
            created_user_name=await get_distinct_values(Task.task_created_user_name),
            updated_user_name=await get_distinct_values(Task.task_updated_user_name),
        )
