"""存储相关 Schema"""

from datetime import datetime

from pydantic import BaseModel, Field


class CollectionRecordCreate(BaseModel):
    """创建采集记录请求"""

    task_id: int | None = None
    user_id: int
    file_size: int = 0
    duration: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    output_dir: str | None = None
    files: str | None = None


class CollectionRecordResponse(BaseModel):
    """采集记录响应"""

    id: int
    cloud_id: int | None = None
    task_id: int | None = None
    user_name: str | None = None
    file_size: int = 0
    duration: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    collection_status: str
    validation_status: str | None = None
    validation_summary: str | None = None
    upload_status: str
    upload_progress: int = 0
    materialize_progress: int = 0
    materialize_error: str | None = None
    raw_bytes: int = 0
    raw_frame_count: int = 0
    output_dir: str | None = None
    raw_capture_dir: str | None = None
    files: list[str] = Field(default_factory=list)
    template_name: str | None = None
    device_type_name: str | None = None
    plan_name: str | None = None
    purpose_name: str | None = None

    model_config = {"from_attributes": True}


class CollectionRecordFilter(BaseModel):
    """采集记录过滤参数"""

    upload_status: str | None = None
    task_id: int | None = None
    user_name: str | None = None
    template_name: str | None = None
    device_type_name: str | None = None
    plan_name: str | None = None


class PaginatedCollectionRecordResponse(BaseModel):
    """分页采集记录响应"""

    items: list[CollectionRecordResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class StorageFilterOptions(BaseModel):
    """存储过滤选项响应"""

    upload_status: list[str]
    user_name: list[str]
    template_name: list[str]
    device_type_name: list[str]
    plan_name: list[str]
