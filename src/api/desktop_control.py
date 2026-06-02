"""桌面端本地控制 API 路由"""

from fastapi import APIRouter, Depends

from src.dependencies import (
    get_robot_command_service,
    validate_desktop_lift_capability,
)
from src.schemas.response import ResponseSchema
from src.schemas.robot_control import LiftHeightRequest, LiftHeightResponse
from src.services.robot_command_service import RobotCommandService

router = APIRouter()


@router.post("/lift/height", response_model=ResponseSchema[LiftHeightResponse])
async def set_lift_height(
    payload: LiftHeightRequest,
    _capability_check: None = Depends(validate_desktop_lift_capability),
    robot_command_service: RobotCommandService = Depends(get_robot_command_service),
):
    """设置 Desktop 本地升降台高度。"""
    result = await robot_command_service.execute_command(
        component_id="slave_arm1",
        action="lift/set_height",
        args={"height": payload.height},
    )
    return ResponseSchema(
        data=LiftHeightResponse(
            height=payload.height,
            component_id="slave_arm1",
            action="lift/set_height",
            result=result.get("result", {}),
        )
    )
