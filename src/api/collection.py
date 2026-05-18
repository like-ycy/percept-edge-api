# src/api/collection.py
"""采集 API 路由"""

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_context import get_app_context
from src.core.exceptions import BusinessError, ValidationError
from src.dependencies import (
    get_collection_service,
    get_current_user,
    get_db,
    get_webrtc_service,
)
from src.schemas.auth import UserInfo
from src.schemas.collection import (
    CollectionLockReleaseRequest,
    CollectionLockState,
    CollectionSession,
)
from src.schemas.response import EmptyData, ResponseSchema
from src.services.collection_lock_service import CollectionLockService
from src.services.collection_service import CollectionService
from src.services.webrtc_service import WebRTCService

router = APIRouter()


class CameraListData(BaseModel):
    """摄像头列表数据"""

    cameras: list[str]


@router.post("/start", response_model=ResponseSchema[CollectionSession])
async def start_collection(
    task_id: int,
    user: UserInfo = Depends(get_current_user),
    service: CollectionService = Depends(get_collection_service),
    db: AsyncSession = Depends(get_db),
):
    """开始采集"""
    session = await service.start_collection(
        task_id=task_id,
        user=user,
        db=db,
    )
    return ResponseSchema(data=session)


@router.post("/stop", response_model=ResponseSchema[CollectionSession])
async def stop_collection(
    user: UserInfo = Depends(get_current_user),
    service: CollectionService = Depends(get_collection_service),
    db: AsyncSession = Depends(get_db),
):
    """停止采集并转入后台整理"""
    session = await service.stop_collection(db=db)
    return ResponseSchema(data=session)


@router.post("/discard", response_model=ResponseSchema[CollectionSession])
async def discard_collection(
    user: UserInfo = Depends(get_current_user),
    service: CollectionService = Depends(get_collection_service),
    db: AsyncSession = Depends(get_db),
):
    """丢弃采集（删除文件，不保存记录，不通知云端）"""
    session = await service.discard_collection(db=db)
    return ResponseSchema(data=session)


@router.get("/status", response_model=ResponseSchema[CollectionSession | EmptyData])
async def get_status(
    service: CollectionService = Depends(get_collection_service),
):
    """获取采集状态"""
    status = service.get_status()
    if status:
        return ResponseSchema(data=status)
    return ResponseSchema(data=EmptyData())


@router.get("/cameras", response_model=ResponseSchema[CameraListData])
async def get_available_cameras(
    webrtc: WebRTCService = Depends(get_webrtc_service),
):
    """获取可用的摄像头列表"""
    cameras = webrtc.get_available_cameras()
    return ResponseSchema(data=CameraListData(cameras=cameras))


@router.websocket("/preview")
async def webrtc_signaling(
    websocket: WebSocket,
    camera_id: str = "camera1",
) -> None:
    """WebRTC 信令 WebSocket"""
    webrtc: WebRTCService = get_app_context(websocket.app).services.webrtc_service
    await websocket.accept()
    client_id = str(id(websocket))
    try:
        offer = await webrtc.create_offer(client_id, camera_id)
        await websocket.send_json({"type": "offer", "data": offer})

        msg = await websocket.receive_json()
        if msg["type"] == "answer":
            await webrtc.handle_answer(client_id, msg["data"])

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await webrtc.close_connection(client_id)


def _state_to_schema(state) -> CollectionLockState:
    return CollectionLockState(
        locked=bool(state.locked),
        reason=state.reason,
        triggered_record_id=state.triggered_record_id,
        triggered_at=state.triggered_at,
        released_at=state.released_at,
        released_by=state.released_by,
        release_note=state.release_note,
    )


def _get_lock_service(request: Request) -> CollectionLockService:
    svc = get_app_context(request.app).services.collection_lock_service
    if svc is None:
        raise BusinessError("采集锁服务未初始化")
    return svc


@router.get("/lock/status", response_model=ResponseSchema[CollectionLockState])
async def get_lock_status(request: Request):
    """查询采集全局锁状态"""
    svc = _get_lock_service(request)
    state = await svc.get_state()
    return ResponseSchema(data=_state_to_schema(state))


@router.post("/lock/release", response_model=ResponseSchema[CollectionLockState])
async def release_lock(payload: CollectionLockReleaseRequest, request: Request):
    """解除采集全局锁"""
    operator = payload.operator.strip()
    if not operator:
        raise ValidationError("operator 不能为空")
    note = payload.note.strip() if payload.note else None

    svc = _get_lock_service(request)
    prev_state = await svc.get_state()
    if not prev_state.locked:
        raise BusinessError("当前未锁定，无需解锁")

    released = await svc.release(operator=operator, note=note)
    if not released:
        raise BusinessError("解锁失败（可能并发已被解锁）")

    logger.warning(
        "采集全局锁已解除，已恢复采集: operator={}, note={}, prev_reason={}",
        operator,
        note,
        prev_state.reason,
    )

    new_state = await svc.get_state()
    return ResponseSchema(data=_state_to_schema(new_state))
