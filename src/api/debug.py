# src/api/debug.py
"""调试 API 路由"""

from fastapi import APIRouter, Depends

from src.core.exceptions import BusinessError
from src.dependencies import get_process_monitor, get_zeromq_consumer
from src.schemas.debug import ZeroMQDebugInfo
from src.schemas.process_monitor import ProcessMonitorResponse
from src.schemas.response import ResponseSchema
from src.services.process_monitor import ProcessMonitor
from src.services.zeromq_consumer import ZeroMQConsumer

router = APIRouter()


@router.get("/zeromq", response_model=ResponseSchema[ZeroMQDebugInfo])
def get_zeromq_debug_info(
    consumer: ZeroMQConsumer = Depends(get_zeromq_consumer),
):
    """获取 ZeroMQ 调试信息"""
    info = consumer.get_debug_info()
    return ResponseSchema(data=info)


@router.get("/processes", response_model=ResponseSchema[ProcessMonitorResponse])
async def get_processes(
    refresh: bool = False,
    monitor: ProcessMonitor | None = Depends(get_process_monitor),
):
    """获取主进程及子进程指标"""
    if monitor is None:
        raise BusinessError("process monitor disabled")
    if refresh:
        data = await monitor.refresh()
        data = data.model_copy(update={"from_cache": False})
    else:
        snapshot = monitor.get_snapshot()
        if snapshot is None:
            data = await monitor.refresh()
            data = data.model_copy(update={"from_cache": False})
        else:
            data = snapshot.model_copy(update={"from_cache": True})
    return ResponseSchema(data=data)
