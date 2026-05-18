# src/schemas/auth.py
"""认证相关模型"""

from pydantic import BaseModel, Field


class UserInfo(BaseModel):
    """IAM 返回的用户信息"""

    user_id: int
    user_name: str = Field(alias="username")
    created_time: int | None = None
