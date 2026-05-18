# src/schemas/query.py
"""查询参数 Schema

使用 Pydantic 的 BeforeValidator 处理空字符串转 None 和类型转换逻辑，
简化 GET 请求参数解析。
"""

from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel, BeforeValidator, Field


def _str_to_int_or_none(v: Any) -> int | None:
    """将字符串转换为 int，空字符串或无效值转换为 None"""
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _empty_str_to_none(v: Any) -> str | None:
    """将空字符串转换为 None"""
    if v == "":
        return None
    return v


def _str_to_page(v: Any) -> int:
    """将字符串转换为页码，空字符串或无效值返回默认值 1"""
    if v is None or v == "":
        return 1
    if isinstance(v, int):
        return max(1, v)
    try:
        return max(1, int(v))
    except (ValueError, TypeError):
        return 1


def _str_to_page_size(v: Any) -> int:
    """将字符串转换为每页数量，空字符串或无效值返回默认值 100"""
    if v is None or v == "":
        return 100
    if isinstance(v, int):
        return min(100, max(1, v))
    try:
        return min(100, max(1, int(v)))
    except (ValueError, TypeError):
        return 100


# 类型别名
StrOrNone = Annotated[str | None, BeforeValidator(_empty_str_to_none)]
IntOrNone = Annotated[int | None, BeforeValidator(_str_to_int_or_none)]
PageInt = Annotated[int, BeforeValidator(_str_to_page)]
PageSizeInt = Annotated[int, BeforeValidator(_str_to_page_size)]


class TaskQueryParams(BaseModel):
    """任务查询参数"""

    # 过滤参数
    status: StrOrNone = Field(Query(None, description="任务状态"))
    task_id: IntOrNone = Field(Query(None, description="云端任务 ID"))
    device_type_name: StrOrNone = Field(Query(None, description="设备类型名称"))
    plan_name: StrOrNone = Field(Query(None, description="采集方案名称"))
    template_name: StrOrNone = Field(Query(None, description="任务模板名称（模糊匹配）"))
    collector_name: StrOrNone = Field(Query(None, description="采集人名称"))
    created_user_name: StrOrNone = Field(Query(None, description="创建人（模糊匹配）"))
    updated_user_name: StrOrNone = Field(Query(None, description="更新人（模糊匹配）"))

    # 分页参数
    page: PageInt = Field(Query(1, description="页码，从 1 开始"))
    page_size: PageSizeInt = Field(Query(100, description="每页数量，最大 100"))

    def has_filters(self) -> bool:
        """检查是否有任何过滤条件"""
        return any(
            [
                self.status,
                self.task_id,
                self.device_type_name,
                self.plan_name,
                self.template_name,
                self.collector_name,
                self.created_user_name,
                self.updated_user_name,
            ]
        )


class StorageQueryParams(BaseModel):
    """存储查询参数"""

    # 过滤参数
    upload_status: StrOrNone = Field(Query(None, description="上传状态"))
    task_id: IntOrNone = Field(Query(None, description="任务 ID"))
    user_name: StrOrNone = Field(Query(None, description="上传用户（模糊匹配）"))
    template_name: StrOrNone = Field(Query(None, description="模板名称（模糊匹配）"))
    device_type_name: StrOrNone = Field(Query(None, description="设备类型名称"))
    plan_name: StrOrNone = Field(Query(None, description="采集方案名称"))

    # 分页参数
    page: PageInt = Field(Query(1, description="页码，从 1 开始"))
    page_size: PageSizeInt = Field(Query(100, description="每页数量，最大 100"))

    def has_filters(self) -> bool:
        """检查是否有任何过滤条件"""
        return any(
            [
                self.upload_status,
                self.task_id,
                self.user_name,
                self.template_name,
                self.device_type_name,
                self.plan_name,
            ]
        )
