"""后台任务同步服务"""

import asyncio
from dataclasses import dataclass
from typing import Callable

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.sync_throttle import SyncThrottle
from src.repositories.task_repo import TaskRepository
from src.schemas.task import SyncResult
from src.services.cloud_client import CloudClient
from src.services.task_service import TaskService
from src.services.task_sync_fetcher import TaskSyncFetcher


@dataclass(slots=True)
class TaskSyncContext:
    """后台任务同步上下文"""

    user_id: int
    username: str
    token: str


_GLOBAL_SYNC_SCOPE = 0


class TaskSyncService:
    """后台任务同步服务"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cloud_client: CloudClient,
        throttle: SyncThrottle,
        interval_seconds: int,
        detail_concurrency: int = 5,
        token_provider: Callable[[], str] | None = None,
    ):
        self._session_factory = session_factory
        self._cloud_client = cloud_client
        self._throttle = throttle
        self._interval_seconds = interval_seconds
        self._fetcher = TaskSyncFetcher(cloud_client, detail_concurrency=detail_concurrency)
        self._contexts: dict[int, TaskSyncContext] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._task: asyncio.Task | None = None
        self._sync_token: str | None = None
        self._token_provider = token_provider

    async def start(self) -> None:
        """启用后台同步服务，等待首次任务请求触发同步循环。"""
        if self._running:
            return
        self._running = True

    async def stop(self) -> None:
        """停止后台同步循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

    def register_user(self, user_id: int, username: str, token: str) -> None:
        """注册任务同步上下文，并更新后台全量同步所需 token。"""
        self._contexts[user_id] = TaskSyncContext(user_id=user_id, username=username, token=token)
        self._sync_token = token
        self._ensure_sync_loop_started()

    async def sync_user(self, user_id: int, force: bool = False) -> SyncResult | None:
        """兼容旧接口，实际执行全量同步。"""
        context = self._contexts.get(user_id)
        if context is not None:
            self._sync_token = context.token
        return await self.sync_all(force=force)

    async def sync_all(self, force: bool = False) -> SyncResult | None:
        """同步云端全部任务。"""
        async with self._lock:
            if not force and not self._throttle.should_sync(_GLOBAL_SYNC_SCOPE):
                return None
            token = self._resolve_sync_token()
            if not token:
                logger.warning("后台任务同步已跳过：尚未获取可用 token")
                return None

            try:
                async with self._session_factory() as session:
                    service = TaskService(
                        repo=TaskRepository(session),
                        cloud_client=self._cloud_client,
                        throttle=self._throttle,
                        fetcher=self._fetcher,
                    )
                    return await service.force_sync_all(token)
            except Exception as exc:
                logger.error(f"后台任务全量同步失败: error={exc}")
                return None

    async def _sync_loop(self) -> None:
        """首次请求触发后，后台按配置间隔刷新本地任务缓存。"""
        while self._running:
            await self.sync_all(force=True)
            await asyncio.sleep(self._interval_seconds)

    def _resolve_sync_token(self) -> str | None:
        """解析后台全量同步可用 token。"""
        if self._sync_token:
            return self._sync_token
        if not self._token_provider:
            return None

        authorization = self._token_provider().strip()
        if not authorization.startswith("Bearer "):
            return None

        token = authorization[7:].strip()
        if not token:
            return None

        self._sync_token = token
        return token

    def _ensure_sync_loop_started(self) -> None:
        """确保后台同步循环仅在首次拿到 token 后启动一次。"""
        if not self._running:
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._sync_loop())
