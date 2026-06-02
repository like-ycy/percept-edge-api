"""上传相关 Schema"""

from datetime import datetime

from pydantic import BaseModel

from src.schemas.status import UploadStatus


class UploadRequest(BaseModel):
    """单条上传请求"""

    record_id: int


class BatchUploadRequest(BaseModel):
    """批量上传请求"""

    record_ids: list[int]


class UploadProgress(BaseModel):
    """上传进度"""

    record_id: int
    status: UploadStatus
    progress: int = 0
    retry_count: int = 0
    error_message: str | None = None


class UploadResult(BaseModel):
    """批量上传结果"""

    success: list[int]
    failed: list[int]


class UploadNotification(BaseModel):
    """上传完成通知数据"""

    record_id: int
    local_path: str
    remote_path: str
    upload_start_time: datetime
    upload_end_time: datetime
    file_size: int
    uploader_id: str
    uploader_name: str
    device_id: str


class CloudDataCreateRequest(BaseModel):
    """云端创建数据请求（POST /data/upload）"""

    task_id: int
    filepath: str
    collector: int
    file_size: int
    file_time: int
    upload_time: int
    end_time: int


class ActiveUploadResponse(BaseModel):
    """活跃上传查询响应"""

    has_active_upload: bool
    records: list[dict] = []
