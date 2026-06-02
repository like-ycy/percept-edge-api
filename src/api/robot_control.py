"""机器人控制 API 路由"""

from fastapi import APIRouter, Depends

from src.dependencies import (
    get_current_user,
    get_robot_command_service,
    validate_electromagnet_capability,
)
from src.schemas.auth import UserInfo
from src.schemas.response import ResponseSchema
from src.schemas.robot_control import ElectromagnetToggleRequest, ElectromagnetToggleResponse
from src.services.robot_command_service import RobotCommandService

router = APIRouter()


@router.post("/electromagnet", response_model=ResponseSchema[ElectromagnetToggleResponse])
async def set_electromagnet(
    request: ElectromagnetToggleRequest,
    _current_user: UserInfo = Depends(get_current_user),
    _capability_check: None = Depends(validate_electromagnet_capability),
    robot_command_service: RobotCommandService = Depends(get_robot_command_service),
):
    """设置 CR5 末端电磁铁开关。"""
    result = await robot_command_service.execute_command(
        component_id="slave_arm1",
        action="tool_do/set",
        args={"enable": request.enabled},
    )
    return ResponseSchema(
        data=ElectromagnetToggleResponse(
            enabled=request.enabled,
            component_id="slave_arm1",
            action="tool_do/set",
            result=result.get("result", {}),
        )
    )
