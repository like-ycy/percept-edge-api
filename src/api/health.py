# src/api/health.py
"""健康检查 API"""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.core.app_context import get_app_context
from src.schemas.response import ResponseSchema

router = APIRouter()


class ServiceStatus(BaseModel):
    """服务状态"""

    status: str  # "ok" | "error"
    message: str | None = None


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str  # "healthy" | "degraded" | "unhealthy"
    cloud_api: ServiceStatus
    robot_os: ServiceStatus
    timestamp: datetime


@router.get("", response_model=ResponseSchema[HealthResponse])
async def health_check(request: Request):
    """健康检查

    检查以下服务的连通性：
    - cloud_api: 云端 API 服务
    - robot_os: 机器人操作系统（通过 ZeroMQ）
    """
    cloud_status = await _check_cloud_api(request)
    robot_status = _check_robot_os(request)

    # 综合判断整体状态
    if cloud_status.status == "ok" and robot_status.status == "ok":
        overall = "healthy"
    elif cloud_status.status == "error" and robot_status.status == "error":
        overall = "unhealthy"
    else:
        overall = "degraded"

    return ResponseSchema(
        data=HealthResponse(
            status=overall,
            cloud_api=cloud_status,
            robot_os=robot_status,
            timestamp=datetime.now(timezone.utc),
        )
    )


async def _check_cloud_api(request: Request) -> ServiceStatus:
    """检查云端 API 连通性"""
    settings = get_app_context(request.app).settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.cloud.base_url}/health")
            if response.status_code == 200:
                return ServiceStatus(status="ok")
            return ServiceStatus(status="error", message=f"HTTP {response.status_code}")
    except httpx.ConnectError:
        return ServiceStatus(status="error", message="连接失败")
    except httpx.TimeoutException:
        return ServiceStatus(status="error", message="连接超时")
    except Exception as e:
        return ServiceStatus(status="error", message=str(e))


def _check_robot_os(request: Request) -> ServiceStatus:
    """检查 robot_os 通信状态（通过 ZeroMQ）"""
    consumer = get_app_context(request.app).services.zeromq_consumer
    debug_info = consumer.get_debug_info()

    if not debug_info.is_connected:
        return ServiceStatus(status="error", message="ZeroMQ 未连接")

    # 检查是否在最近 5 秒内收到过数据
    if debug_info.last_receive_time:
        elapsed = (datetime.now(timezone.utc) - debug_info.last_receive_time).total_seconds()
        if elapsed > 5:
            return ServiceStatus(status="error", message=f"超过 {int(elapsed)} 秒未收到数据")
        return ServiceStatus(status="ok")

    return ServiceStatus(status="error", message="尚未收到任何数据")
