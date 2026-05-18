"""请求上下文管理

使用 contextvars 在异步环境中管理请求级别的状态，
避免在调用链中层层传递 token 等信息。
"""

from contextvars import ContextVar

# 当前请求的 token（不含 Bearer 前缀）
_current_token: ContextVar[str | None] = ContextVar("current_token", default=None)


def get_current_token() -> str | None:
    """获取当前请求的 token"""
    return _current_token.get()


def set_current_token(token: str | None) -> None:
    """设置当前请求的 token"""
    _current_token.set(token)
