"""本地已上传采集目录清理 API Schema。"""

from datetime import datetime

from pydantic import BaseModel, Field

CLEANUP_CONFIRM_TEXT = "DELETE UPLOADED COLLECTIONS"


class CleanupItemResponse(BaseModel):
    """单条清理预览或执行结果。"""

    record_id: int
    bucket: str
    path: str | None = None
    reason: str
    size_bytes: int = 0
    error: str | None = None
    end_time: datetime | None = None
    output_dir: str | None = None
    upload_status: str | None = None
    cloud_id: int | None = None
    cloud_notified: bool = False


class CleanupSummaryResponse(BaseModel):
    """清理计划统计。"""

    eligible_count: int
    missing_count: int
    unsafe_count: int
    deleted_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    reclaimable_bytes: int


class CleanupPreviewResponse(BaseModel):
    """清理预览响应。"""

    cutoff: datetime
    summary: CleanupSummaryResponse
    eligible: list[CleanupItemResponse]
    missing: list[CleanupItemResponse]
    unsafe: list[CleanupItemResponse]


class CleanupExecuteRequest(BaseModel):
    """执行清理请求。"""

    older_than_days: int = Field(default=3, ge=1)
    record_ids: list[int] = Field(min_length=1)
    confirm_text: str


class CleanupExecuteResponse(BaseModel):
    """执行清理响应。"""

    cutoff: datetime
    summary: CleanupSummaryResponse
    deleted: list[CleanupItemResponse]
    failed: list[CleanupItemResponse]
    missing: list[CleanupItemResponse]
    unsafe: list[CleanupItemResponse]
    skipped_ids: list[int] = Field(default_factory=list)
