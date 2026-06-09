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

TRANSLATION_RAIL_COMPONENT_ID = "translation_rail"
LIFT_HEIGHT_ACTION = "lift/set_height"


@router.post("/lift/height", response_model=ResponseSchema[LiftHeightResponse])
async def set_lift_height(
    payload: LiftHeightRequest,
    _capability_check: None = Depends(validate_desktop_lift_capability),
    robot_command_service: RobotCommandService = Depends(get_robot_command_service),
):
    """设置 Desktop 本地升降台高度。"""
    result = await robot_command_service.execute_command(
        component_id=TRANSLATION_RAIL_COMPONENT_ID,
        action=LIFT_HEIGHT_ACTION,
        args={"height": payload.height},
    )
    return ResponseSchema(
        data=LiftHeightResponse(
            height=payload.height,
            component_id=TRANSLATION_RAIL_COMPONENT_ID,
            action=LIFT_HEIGHT_ACTION,
            result=result.get("result", {}),
        )
    )
