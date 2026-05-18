# src/api/monitor.py
"""Monitor API 路由"""

from fastapi import APIRouter, Depends

from src.dependencies import get_monitor_service
from src.schemas.monitor import RobotStatus, SystemInfo
from src.schemas.response import ResponseSchema
from src.services.monitor_service import MonitorService

router = APIRouter()


@router.get("/system", response_model=ResponseSchema[SystemInfo])
def get_system_info(
    monitor: MonitorService = Depends(get_monitor_service),
):
    """获取系统资源信息（CPU/内存/磁盘/平台）"""
    info = monitor.get_system_info()
    if info is None:
        return ResponseSchema(data=None, msg="monitor 数据未就绪")
    return ResponseSchema(data=info)


@router.get("/robot", response_model=ResponseSchema[RobotStatus])
def get_robot_status(
    monitor: MonitorService = Depends(get_monitor_service),
):
    """获取机器人组件状态"""
    status = monitor.get_robot_status()
    if status is None:
        return ResponseSchema(data=None, msg="monitor 数据未就绪")
    return ResponseSchema(data=status)


@router.post("/refresh", response_model=ResponseSchema[dict])
async def refresh_monitor(
    monitor: MonitorService = Depends(get_monitor_service),
):
    """强制刷新 monitor 缓存"""
    success = await monitor.refresh()
    if success:
        return ResponseSchema(data={"message": "刷新成功"})
    return ResponseSchema(data={"message": "刷新失败，请检查 ZeroMQ 连接"})
