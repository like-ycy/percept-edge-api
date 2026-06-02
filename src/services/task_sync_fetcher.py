"""任务同步抓取器"""

import asyncio

from loguru import logger

from src.schemas.status import TaskStatus
from src.schemas.task import CloudTask, CloudTaskDetail
from src.services.cloud_client import CloudClient


class TaskSyncFetcher:
    """云端任务同步抓取器"""

    def __init__(self, cloud_client: CloudClient, detail_concurrency: int = 5):
        self._cloud_client = cloud_client
        self._detail_concurrency = max(1, detail_concurrency)

    async def fetch_task_details(self, token: str) -> tuple[list[CloudTaskDetail], set[int]]:
        """抓取需要同步的任务详情"""
        templates = await self._cloud_client.get_templates(token)
        logger.info(f"开始任务同步抓取: templates={len(templates)}")

        task_lists = await asyncio.gather(
            *(self._cloud_client.get_task_list(token, template.id) for template in templates)
        )

        unique_tasks: dict[int, CloudTask] = {}
        for task_list in task_lists:
            for task in task_list:
                if task.status == TaskStatus.UNRELEASED.value:
                    continue
                unique_tasks.setdefault(task.id, task)

        semaphore = asyncio.Semaphore(self._detail_concurrency)
        details = await asyncio.gather(
            *(self._fetch_single_detail(token, task, semaphore) for task in unique_tasks.values())
        )

        synced_details = [detail for detail in details if detail is not None]
        logger.info(
            "任务同步抓取完成: candidates={}, synced={}, concurrency={}",
            len(unique_tasks),
            len(synced_details),
            self._detail_concurrency,
        )
        return synced_details, set(unique_tasks)

    async def _fetch_single_detail(
        self,
        token: str,
        task: CloudTask,
        semaphore: asyncio.Semaphore,
    ) -> CloudTaskDetail | None:
        """抓取单个任务详情"""
        async with semaphore:
            detail = await self._cloud_client.get_task_detail(token, task.id)
        if not detail:
            logger.warning(f"获取任务详情失败: task_id={task.id}")
            return None
        detail.name = task.name
        return detail
