# -*- coding: utf-8 -*-
"""全局统一响应模型"""

from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class ResponseSchema(BaseModel, Generic[T]):
    """全局统一响应格式"""

    code: int = 200
    msg: str = "success"
    data: T


class EmptyData(BaseModel):
    """空数据占位"""

    pass
