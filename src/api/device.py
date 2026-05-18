"""设备激活 API 路由"""

from fastapi import APIRouter, Depends, Request

from src.core.app_context import get_app_context
from src.core.context import set_current_token
from src.core.exceptions import BusinessError, ValidationError
from src.dependencies import get_cloud_client, get_monitor_service
from src.schemas.device import (
    CloudDeviceActivateRequest,
    CloudDeviceType,
    DeviceActivationRequest,
    DeviceActivationResult,
    DeviceActivationStatusResult,
)
from src.schemas.response import ResponseSchema
from src.services.cloud_client import CloudClient
from src.services.monitor_service import MonitorService

router = APIRouter()


@router.post("/activate", response_model=ResponseSchema[DeviceActivationResult])
async def activate_device(
    request_ctx: Request,
    request: DeviceActivationRequest,
    monitor: MonitorService = Depends(get_monitor_service),
    cloud_client: CloudClient = Depends(get_cloud_client),
):
    """设备激活

    用户只需传入激活码（可选 desc）。
    设备信息（device_type/mac/address 等）从 monitor 缓存自动组装。
    """
    system_info = monitor.get_system_info()
    robot_status = monitor.get_robot_status()
    if system_info is None or robot_status is None:
        raise BusinessError("monitor 数据未就绪，无法激活设备")

    activation_code = request.activation_code.strip()
    if not activation_code:
        raise ValidationError("activation_code 不能为空")

    mac = (system_info.platform.mac_address or "").strip()
    ip_address = (system_info.platform.ip_address or "").strip()
    if not mac:
        raise ValidationError("monitor 缺少 platform.mac_address")
    if not ip_address:
        raise ValidationError("monitor 缺少 platform.ip_address")

    register_info = robot_status.metadata.robot_register_info or {}
    device_name = str(register_info.get("device_type") or robot_status.metadata.robot_model).strip()
    embodied = str(register_info.get("embodied") or "").strip()
    end_type = str(register_info.get("eef_type") or "").strip()
    camera = str(register_info.get("camera") or "").strip()

    missing_fields = []
    if not device_name:
        missing_fields.append("robot.metadata.robot_register_info.device_type")
    if not embodied:
        missing_fields.append("robot.metadata.robot_register_info.embodied")
    if not end_type:
        missing_fields.append("robot.metadata.robot_register_info.eef_type")
    if not camera:
        missing_fields.append("robot.metadata.robot_register_info.camera")

    if missing_fields:
        raise ValidationError(f"monitor 缺少设备注册字段: {', '.join(missing_fields)}")

    desc = request.desc.strip()
    if not desc:
        desc = "\n".join(robot_status.metadata.robot_desc).strip()

    payload = CloudDeviceActivateRequest(
        device_type=CloudDeviceType(
            name=device_name,
            embodied=embodied,
            end_type=end_type,
            camera=camera,
        ),
        mac=mac,
        activation_code=activation_code,
        address=ip_address,
        desc=desc,
    )

    auth_header = request_ctx.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    set_current_token(token)
    try:
        result = await cloud_client.activate_equipment(payload)
    finally:
        set_current_token(None)

    context = get_app_context(request_ctx.app)
    context.runtime.update_device_status(True, result.uid)
    if token:
        authorization = f"Bearer {token}"
        context.runtime.update_authorization(authorization)
    heartbeat = context.services.heartbeat
    if heartbeat:
        heartbeat.update_device_status(True, result.uid)
        if token:
            await heartbeat.send_heartbeat_now()

    return ResponseSchema(data=result)


@router.get(
    "/activation-status",
    response_model=ResponseSchema[DeviceActivationStatusResult],
)
async def get_activation_status(
    request_ctx: Request,
    monitor: MonitorService = Depends(get_monitor_service),
    cloud_client: CloudClient = Depends(get_cloud_client),
):
    """查询设备激活状态"""
    system_info = monitor.get_system_info()
    if system_info is None:
        raise BusinessError("monitor 数据未就绪，无法查询激活状态")

    mac = (system_info.platform.mac_address or "").strip()
    if not mac:
        raise ValidationError("monitor 缺少 platform.mac_address")

    # 查询激活状态不依赖 token，只负责读取云端状态并更新全局缓存
    result = await cloud_client.get_activation_status(mac)

    context = get_app_context(request_ctx.app)
    context.runtime.update_device_status(result.state, result.uid)
    heartbeat = context.services.heartbeat
    if heartbeat:
        heartbeat.update_device_status(result.state, result.uid)

    return ResponseSchema(data=result)
