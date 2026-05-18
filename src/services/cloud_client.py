# src/services/cloud_client.py
"""云端 API 客户端"""

from typing import Any, Optional, TypeVar

import httpx
from loguru import logger
from pydantic import BaseModel
from tenacity import AsyncRetrying, RetryError, stop_after_attempt, wait_exponential_jitter

from src.config import Settings
from src.core.context import get_current_token
from src.core.exceptions import BusinessError, ExternalServiceError
from src.schemas.auth import UserInfo
from src.schemas.device import (
    CloudDeviceActivateRequest,
    DeviceActivationResult,
    DeviceActivationStatusResult,
)
from src.schemas.task import CloudTask, CloudTaskDetail, CloudTemplate
from src.schemas.upload import CloudDataCreateRequest


CLOUD_LIST_PAGE_SIZE = 100
TCloudListItem = TypeVar("TCloudListItem", bound=BaseModel)


class CloudClient:
    """云端客户端，负责与云端 API 通信"""

    def __init__(self, settings: Settings):
        """初始化客户端"""
        self.base_url = settings.cloud.base_url
        self.timeout = settings.cloud.timeout
        self._client = httpx.AsyncClient(timeout=self.timeout)
        # 认证配置
        self._auth_verify_endpoint = settings.auth.iam.verify_endpoint
        self._auth_timeout = settings.auth.iam.timeout
        # 数据上传通知配置
        self._notify_endpoint = settings.upload.notify_endpoint
        self._notify_timeout = settings.upload.notify_timeout
        self._notify_retries = settings.upload.notify_retries

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        await self._client.aclose()

    # ========== 内部辅助方法 ==========

    def _get_auth_headers(self, token: str) -> dict[str, str]:
        """生成认证请求头

        Args:
            token: 原始 Token

        Returns:
            包含 Authorization 的字典
        """
        return {"Authorization": token if token.startswith("Bearer ") else f"Bearer {token}"}

    async def _perform_request(
        self,
        method: str,
        endpoint: str,
        *,
        token: Optional[str] = None,
        json_data: Optional[dict[str, Any]] = None,
        query_params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
        context: str = "请求",
    ) -> httpx.Response:
        """统一执行 HTTP 请求并处理通用异常

        Args:
            method: HTTP 方法
            endpoint: API 端点
            token: 认证 Token
            json_data: JSON 请求体
            timeout: 超时时间
            context: 上下文描述

        Returns:
            httpx.Response 对象

        Raises:
            ExternalServiceError: 网络错误或未知异常
        """
        url = f"{self.base_url}{endpoint}"
        headers = self._get_auth_headers(token) if token else {}

        try:
            return await self._client.request(
                method,
                url,
                headers=headers,
                json=json_data,
                params=query_params,
                timeout=timeout,
            )
        except httpx.RequestError as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"{context}异常: {error_msg}")
            raise ExternalServiceError("CloudAPI", error_msg)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"{context}未知异常: {error_msg}")
            raise ExternalServiceError("CloudAPI", error_msg)

    # ========== 认证相关 ==========

    async def verify_token(self, token: str) -> Optional[UserInfo]:
        """校验 Token 并返回用户信息

        Args:
            token: Bearer Token（会自动添加 Bearer 前缀）

        Returns:
            校验成功返回 UserInfo，失败返回 None

        Raises:
            ExternalServiceError: 认证服务请求失败
        """
        try:
            response = await self._perform_request(
                "GET",
                self._auth_verify_endpoint,
                token=token,
                timeout=self._auth_timeout,
                context="验证 Token",
            )

            if response.status_code in (401, 403):
                logger.warning(f"认证请求失败: status={response.status_code}, body={response.text}")
                return None

            if response.status_code in (429, 500, 502, 503, 504):
                logger.error(f"认证服务异常: status={response.status_code}, body={response.text}")
                raise ExternalServiceError("CloudAPI", f"HTTP {response.status_code}")

            if response.status_code != 200:
                logger.warning(f"认证请求失败: status={response.status_code}, body={response.text}")
                return None

            result = response.json()
            if result.get("code") == 200:
                return UserInfo.model_validate(result["data"])

            logger.warning(f"认证返回错误: {result}")
            return None

        except ExternalServiceError:
            raise

    # ========== 任务同步相关 ==========

    async def _fetch_paginated_list(
        self,
        endpoint: str,
        *,
        token: str,
        context: str,
        model_type: type[TCloudListItem],
    ) -> list[TCloudListItem]:
        """按云端 total 拉取完整分页列表。"""
        page = 1
        items: list[TCloudListItem] = []

        while True:
            response = await self._perform_request(
                "GET",
                endpoint,
                token=token,
                query_params={"page": page, "page_size": CLOUD_LIST_PAGE_SIZE},
                context=context,
            )

            try:
                response.raise_for_status()
                data = response.json()
                if data is None:
                    return items
                data_field = data.get("data")
                if data_field is None:
                    return items

                page_items = data_field.get("list", [])
                items.extend(model_type.model_validate(item) for item in page_items)

                total = self._parse_total(data_field.get("total"), len(items))
                if len(items) >= total or not page_items:
                    return items

                page += 1
            except httpx.HTTPStatusError as e:
                logger.error(f"{context}失败: HTTP {e.response.status_code}")
                raise ExternalServiceError("CloudAPI", f"HTTP {e.response.status_code}")

    @staticmethod
    def _parse_total(value: Any, default: int) -> int:
        """解析云端分页 total，异常格式回退到当前已获取数量。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    async def get_templates(self, token: str) -> list[CloudTemplate]:
        """获取模版列表

        调用 GET /template/list 接口获取所有模板。
        """
        return await self._fetch_paginated_list(
            "/template/list",
            token=token,
            context="获取模版列表",
            model_type=CloudTemplate,
        )

    async def get_task_list(self, token: str, template_id: int) -> list[CloudTask]:
        """获取任务列表

        调用 GET /task/{template_id}/list 接口获取指定模板下的所有任务。
        """
        return await self._fetch_paginated_list(
            f"/task/{template_id}/list",
            token=token,
            context="获取任务列表",
            model_type=CloudTask,
        )

    async def get_task_detail(self, token: str, task_id: int) -> Optional[CloudTaskDetail]:
        """获取任务详情

        调用 GET /task/{task_id} 接口获取任务完整详情。
        """
        response = await self._perform_request(
            "GET", f"/task/{task_id}", token=token, context="获取任务详情"
        )

        try:
            response.raise_for_status()
            data = response.json()
            task_data = data.get("data")
            if task_data:
                return CloudTaskDetail(**task_data)
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"获取任务详情失败: HTTP {e.response.status_code}")
            raise ExternalServiceError("CloudAPI", f"HTTP {e.response.status_code}")

    # ========== 数据上传相关 ==========

    async def create_data(
        self,
        request_data: CloudDataCreateRequest,
        *,
        token: Optional[str] = None,
    ) -> Optional[int]:
        """创建采集数据记录（带重试）

        调用 POST /data/upload 接口通知云端上传完成。
        使用 tenacity 实现指数退避 + 抖动，避免惊群效应。
        token 从请求上下文自动获取。

        Args:
            request_data: 数据创建请求

        Returns:
            云端数据 ID，失败返回 None
        """
        if not self.base_url:
            return None

        token = token or get_current_token()
        if not token:
            logger.error("创建数据记录失败: 缺少可用 token")
            return None

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._notify_retries),
                wait=wait_exponential_jitter(initial=1, max=30, jitter=3),
                reraise=True,
            ):
                with attempt:
                    response = await self._perform_request(
                        "POST",
                        self._notify_endpoint,
                        token=token,
                        json_data=request_data.model_dump(),
                        timeout=self._notify_timeout,
                        context="创建数据记录",
                    )

                    if response.status_code == 200:
                        resp = response.json()
                        if resp.get("code") == 200:
                            cloud_id = resp.get("data", {}).get("id")
                            if isinstance(cloud_id, int):
                                logger.info(f"云端数据创建成功: id={cloud_id}")
                                return cloud_id

                            logger.error(f"云端数据创建成功但缺少有效 id: {resp}")
                            return None
                        else:
                            logger.error(f"云端数据创建失败: {resp.get('msg', '未知错误')}")
                    else:
                        logger.error(
                            f"云端数据创建响应异常: {response.status_code}, {response.text}"
                        )

        except (RetryError, ExternalServiceError):
            logger.error(f"云端数据创建最终失败: task_id={request_data.task_id}")
            return None
        return None

    async def delete_data(self, data_id: int, *, token: Optional[str] = None) -> bool:
        """删除云端采集数据记录。

        调用 DELETE /data/{data_id} 接口。

        Args:
            data_id: 云端数据 ID
            token: 可选 token，不传则从上下文获取

        Returns:
            删除成功返回 True，否则 False
        """
        if not self.base_url:
            return False

        token = token or get_current_token()
        if not token:
            logger.error("删除云端数据失败: 缺少可用 token")
            return False

        response = await self._perform_request(
            "DELETE",
            f"/data/{data_id}",
            token=token,
            context="删除云端数据",
        )

        if response.status_code != 200:
            logger.error(f"删除云端数据响应异常: {response.status_code}, {response.text}")
            return False

        result = response.json()
        if result.get("code") == 200:
            return True

        logger.error(f"删除云端数据失败: {result.get('msg', '未知错误')}")
        return False

    # ========== 设备激活相关 ==========

    async def activate_equipment(
        self, request_data: CloudDeviceActivateRequest
    ) -> DeviceActivationResult:
        """激活设备

        调用 POST /equipment/activate 接口。
        token 从请求上下文自动获取（若无则不带 Authorization）。

        Args:
            request_data: 设备激活请求参数

        Returns:
            设备激活结果（id/uid）

        Raises:
            ExternalServiceError: 网络异常或 HTTP 错误
            BusinessError: 云端业务返回 code != 200
        """
        token = get_current_token()

        response = await self._perform_request(
            "POST",
            "/equipment/activate",
            token=token,
            json_data=request_data.model_dump(),
            context="设备激活",
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"设备激活失败: HTTP {e.response.status_code}, body={e.response.text}")
            raise ExternalServiceError("CloudAPI", f"HTTP {e.response.status_code}")

        result = response.json()
        if result.get("code") != 200:
            raise BusinessError(result.get("msg", "设备激活失败"))

        data = result.get("data")
        if not isinstance(data, dict):
            raise ExternalServiceError("CloudAPI", "设备激活响应缺少 data")

        return DeviceActivationResult.model_validate(data)

    async def get_activation_status(self, mac: str) -> DeviceActivationStatusResult:
        """查询设备激活状态

        调用 GET /equipment/activation_status?mac=... 接口。
        token 从请求上下文自动获取（若无则不带 Authorization）。
        """
        token = get_current_token()

        response = await self._perform_request(
            "GET",
            "/equipment/activation_status",
            token=token,
            query_params={"mac": mac},
            context="查询激活状态",
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"查询激活状态失败: HTTP {e.response.status_code}, body={e.response.text}")
            raise ExternalServiceError("CloudAPI", f"HTTP {e.response.status_code}")

        result = response.json()
        if result.get("code") != 200:
            raise BusinessError(result.get("msg", "查询激活状态失败"))

        data = result.get("data")
        if not isinstance(data, dict):
            raise ExternalServiceError("CloudAPI", "查询激活状态响应缺少 data")

        return DeviceActivationStatusResult.model_validate(data)
