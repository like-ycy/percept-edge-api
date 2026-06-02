# src/services/webrtc_service.py
"""WebRTC 推流服务"""

import cv2
import numpy as np
from aiortc import RTCPeerConnection, VideoStreamTrack
from av import VideoFrame

from src.services.zeromq_consumer import ZeroMQConsumer


class CameraVideoTrack(VideoStreamTrack):
    """单个摄像头的视频轨道"""

    def __init__(self, consumer: ZeroMQConsumer, camera_id: str):
        """初始化视频轨道

        Args:
            consumer: ZeroMQ 消费者实例
            camera_id: 摄像头 ID，如 "camera1"
        """
        super().__init__()
        self.consumer = consumer
        self.camera_id = camera_id

    async def recv(self) -> VideoFrame:
        """接收下一帧

        Returns:
            VideoFrame 对象
        """
        pts, time_base = await self.next_timestamp()
        frame_data = self.consumer.get_latest_frame()

        img = None
        if frame_data is not None:
            # 查找指定摄像头的 RGB 数据
            for camera in frame_data.cameras:
                if camera.component_id == self.camera_id and camera.color_data:
                    img = cv2.imdecode(
                        np.frombuffer(camera.color_data, dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    if img is not None:
                        # BGR -> RGB
                        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    break

        if img is None:
            # 无帧时返回黑色图像
            img = np.zeros((480, 640, 3), dtype=np.uint8)

        frame = VideoFrame.from_ndarray(img, format="rgb24")  # type: ignore[arg-type]
        frame.pts = pts
        frame.time_base = time_base
        return frame


class WebRTCService:
    """WebRTC 推流服务"""

    def __init__(self, consumer: ZeroMQConsumer):
        """初始化 WebRTC 服务

        Args:
            consumer: ZeroMQ 消费者实例
        """
        self.consumer = consumer
        self._connections: dict[str, RTCPeerConnection] = {}

    def get_available_cameras(self) -> list[str]:
        """获取当前可用的摄像头列表

        Returns:
            摄像头 ID 列表
        """
        frame = self.consumer.get_latest_frame()
        if frame is None:
            return []
        return [camera.component_id for camera in frame.cameras if camera.color_data is not None]

    async def create_offer(self, client_id: str, camera_id: str = "camera1") -> dict:
        """创建 WebRTC offer

        Args:
            client_id: 客户端标识
            camera_id: 摄像头 ID，默认 "camera1"

        Returns:
            包含 sdp 和 type 的字典
        """
        pc = RTCPeerConnection()
        self._connections[client_id] = pc

        video_track = CameraVideoTrack(self.consumer, camera_id)
        pc.addTrack(video_track)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    async def handle_answer(self, client_id: str, answer: dict) -> None:
        """处理客户端的 answer

        Args:
            client_id: 客户端标识
            answer: 包含 sdp 和 type 的字典
        """
        pc = self._connections.get(client_id)
        if pc:
            from aiortc import RTCSessionDescription

            await pc.setRemoteDescription(RTCSessionDescription(**answer))

    async def close_connection(self, client_id: str) -> None:
        """关闭指定客户端的连接

        Args:
            client_id: 客户端标识
        """
        pc = self._connections.pop(client_id, None)
        if pc:
            await pc.close()
