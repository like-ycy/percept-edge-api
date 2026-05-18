# src/schemas/task.py
"""任务相关 Schema"""

from datetime import datetime

from pydantic import BaseModel, Field

# ============ 云端数据 Schema ============


class IdNamePair(BaseModel):
    """通用 ID-Name 对"""

    id: int
    name: str


class CloudTemplate(BaseModel):
    """云端模板（GET /template/list 响应）"""

    id: int
    name: str
    task_type_name: str | None = None
    purpose: str | None = None
    device_type_name: str | None = None
    plan: str | None = None
    task_count: int = 0
    published_count: int = 0
    completed_count: int = 0
    created_time: int | None = None
    updated_time: int | None = None
    created_user_name: str | None = None
    updated_user_name: str | None = None


class CloudTaskTemplate(BaseModel):
    """任务详情中的模板信息"""

    id: int
    name: str
    desc: str | None = None
    task_type: IdNamePair | None = None
    device_type: IdNamePair | None = None
    plan: IdNamePair | None = None
    difficulty: IdNamePair | None = None
    purpose: IdNamePair | None = None
    case: str = ""
    initial_state: str = ""


class CloudCollector(BaseModel):
    """采集人信息"""

    id: int
    username: str


class CloudUser(BaseModel):
    """用户信息"""

    id: int
    username: str


class CloudStep(BaseModel):
    """任务步骤"""

    gap: int = 0
    index: int = 0
    action: str = ""
    duration: int = 0


class CloudTaskDetail(BaseModel):
    """云端任务详情（GET /task/{task_id} 响应）"""

    id: int
    name: str | None = None
    template: CloudTaskTemplate
    step_count: int = 0
    repeat: int = 1
    step: list[CloudStep] = []
    collector: CloudCollector | None = None
    progress: int = 0
    case: str = ""
    instructions: list[str] = Field(default_factory=list)
    published: bool = False
    status: str  # 移除默认值，强制云端必须返回状态
    created_time: int | None = None
    updated_time: int | None = None
    created_user: CloudUser | None = None
    updated_user: CloudUser | None = None


class CloudTask(BaseModel):
    """云端任务（GET /task/{template_id}/list 响应，简化版）"""

    id: int
    name: str | None = None
    step_count: int = 0
    repeat: int = 1
    progress: int = 0
    created_time: int | None = None
    created_user_name: str | None = None
    updated_time: int | None = None
    updated_user_name: str | None = None
    status: str  # 移除默认值，强制云端必须返回状态


# ============ 本地响应 Schema ============


class StepResponse(BaseModel):
    """步骤响应"""

    gap: int = 0
    index: int = 0
    action: str = ""
    duration: int = 0


class TaskResponse(BaseModel):
    """任务响应（合并模板和任务数据）"""

    # 本地主键
    id: int

    # 模板字段
    template_name: str
    template_desc: str | None = None
    purpose_name: str | None = None
    device_type_name: str | None = None
    plan_name: str | None = None
    initial_state: str = ""

    # 任务字段
    task_id: int
    task_name: str | None = None
    repeat: int = 1
    progress: int = 0
    steps: list[StepResponse] = []
    case: str = ""
    instructions: list[str] = Field(default_factory=list)
    status: str = "run"
    collector_name: str | None = None
    task_created_user_name: str | None = None
    task_updated_user_name: str | None = None

    model_config = {"from_attributes": True}


class TaskListData(BaseModel):
    """任务列表数据"""

    tasks: list[TaskResponse]
    last_sync: datetime | None = None


class SyncResult(BaseModel):
    """同步结果"""

    added: int
    updated: int
    deleted: int
    total: int


# ============ 过滤和分页 Schema ============


class TaskFilter(BaseModel):
    """任务过滤参数"""

    status: str | None = None  # 任务状态
    task_id: int | None = None  # 云端任务 ID
    device_type_name: str | None = None  # 设备类型名称
    plan_name: str | None = None  # 采集方案名称
    template_name: str | None = None  # 任务模板名称（模糊匹配）
    collector_name: str | None = None  # 采集人名称
    created_user_name: str | None = None  # 创建人（模糊匹配）
    updated_user_name: str | None = None  # 更新人（模糊匹配）


class PaginationParams(BaseModel):
    """分页参数"""

    page: int = 1  # 页码，从 1 开始
    page_size: int = 20  # 每页数量，默认 20

    model_config = {"extra": "forbid"}


class PaginatedTaskResponse(BaseModel):
    """分页任务响应"""

    items: list[TaskResponse]
    total: int  # 总记录数
    page: int  # 当前页码
    page_size: int  # 每页数量
    total_pages: int  # 总页数


class TaskFilterOptions(BaseModel):
    """任务过滤选项响应"""

    status: list[str]  # 任务状态选项
    device_type_name: list[str]  # 设备类型选项
    plan_name: list[str]  # 采集方案选项
    template_name: list[str]  # 模板名称选项
    collector_name: list[str]  # 采集人选项
    created_user_name: list[str]  # 创建人选项
    updated_user_name: list[str]  # 更新人选项
