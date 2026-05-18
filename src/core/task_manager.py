# src/core/task_manager.py
"""后台任务管理器"""

import asyncio
from typing import Any, Callable, Coroutine

from loguru import logger


class BackgroundTaskManager:
    """管理后台异步任务的执行

    支持两种类型的任务：
    - 普通任务：应用关闭时会被取消
    - 关键任务：应用关闭时会等待其完成（优雅关闭）
    """

    def __init__(self, max_concurrency: int = 5, shutdown_timeout: float = 30.0):
        """初始化后台任务管理器

        Args:
            max_concurrency: 最大并发任务数
            shutdown_timeout: 关闭时等待关键任务完成的超时时间（秒）
        """
        self._tasks: dict[str, asyncio.Task] = {}
        self._critical_tasks: dict[str, asyncio.Task] = {}  # 关键任务，关闭时等待完成
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._shutdown_timeout = shutdown_timeout
        self._shutting_down = False

    def create_task(
        self,
        task_id: str,
        coro: Coroutine[Any, Any, Any],
        on_complete: Callable[[str, Any], None] | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
        critical: bool = False,
    ) -> asyncio.Task | None:
        """创建并启动后台任务

        Args:
            task_id: 任务唯一标识
            coro: 要执行的协程
            on_complete: 任务完成回调
            on_error: 任务失败回调
            critical: 是否为关键任务（关闭时等待完成而不取消）

        Returns:
            创建的 asyncio.Task 对象，如果正在关闭则返回 None
        """
        # 正在关闭时不接受新任务
        if self._shutting_down:
            logger.warning(f"应用正在关闭，拒绝创建任务: {task_id}")
            coro.close()
            return None

        task_store = self._critical_tasks if critical else self._tasks
        started = False

        async def wrapper():
            nonlocal started
            async with self._semaphore:
                started = True
                try:
                    result = await coro
                    if on_complete:
                        on_complete(task_id, result)
                    return result
                except asyncio.CancelledError:
                    logger.debug(f"后台任务 {task_id} 被取消")
                    raise
                except Exception as e:
                    logger.exception(f"后台任务 {task_id} 失败")
                    if on_error:
                        on_error(task_id, e)
                    raise
                finally:
                    task_store.pop(task_id, None)

        task = asyncio.create_task(wrapper())

        def close_unstarted_coro(_task: asyncio.Task) -> None:
            task_store.pop(task_id, None)
            if not started:
                coro.close()

        task.add_done_callback(close_unstarted_coro)
        task_store[task_id] = task
        return task

    def cancel_task(self, task_id: str) -> bool:
        """取消指定任务

        Args:
            task_id: 任务唯一标识

        Returns:
            是否成功取消
        """
        if task := self._tasks.get(task_id):
            task.cancel()
            return True
        if task := self._critical_tasks.get(task_id):
            task.cancel()
            return True
        return False

    def get_pending_count(self) -> tuple[int, int]:
        """获取待处理任务数量

        Returns:
            (普通任务数, 关键任务数)
        """
        return len(self._tasks), len(self._critical_tasks)

    def get_tasks_by_prefix(self, prefix: str) -> dict[str, asyncio.Task]:
        """按前缀查询任务"""
        result = {}
        for task_id, task in self._critical_tasks.items():
            if task_id.startswith(prefix):
                result[task_id] = task
        for task_id, task in self._tasks.items():
            if task_id.startswith(prefix):
                result[task_id] = task
        return result

    def get_critical_task_ids(self) -> list[str]:
        """获取所有关键任务 ID"""
        return list(self._critical_tasks.keys())

    async def shutdown(self) -> None:
        """优雅关闭所有任务

        - 普通任务：立即取消
        - 关键任务：等待完成（带超时）
        """
        self._shutting_down = True
        logger.info(
            f"开始关闭后台任务管理器，普通任务: {len(self._tasks)}，关键任务: {len(self._critical_tasks)}"
        )

        # 1. 取消所有普通任务
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

        # 2. 等待关键任务完成（带超时）
        if self._critical_tasks:
            logger.info(
                f"等待 {len(self._critical_tasks)} 个关键任务完成（超时: {self._shutdown_timeout}s）..."
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._critical_tasks.values(), return_exceptions=True),
                    timeout=self._shutdown_timeout,
                )
                logger.info("所有关键任务已完成")
            except asyncio.TimeoutError:
                logger.warning(f"关键任务等待超时，强制取消剩余 {len(self._critical_tasks)} 个任务")
                for task in self._critical_tasks.values():
                    task.cancel()
                await asyncio.gather(*self._critical_tasks.values(), return_exceptions=True)
            self._critical_tasks.clear()

        logger.info("后台任务管理器已关闭")
