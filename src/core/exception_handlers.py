# src/core/exception_handlers.py
"""全局异常处理器

提供统一的异常处理和响应格式，确保所有错误都返回一致的 JSON 结构。
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from src.core.exceptions import (
    AppException,
    NotFoundError,
    ValidationError,
    BusinessError,
    ExternalServiceError,
)
from src.core.logging import logger


def _error_response(
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    """构建统一的错误响应

    Args:
        status_code: HTTP 状态码
        code: 业务错误码
        message: 错误描述

    Returns:
        JSONResponse 对象
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "msg": message,
            "data": None,
            "error_code": code,  # 业务错误码，便于客户端识别
        },
    )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """处理业务异常

    根据异常类型返回对应的 HTTP 状态码：
    - NotFoundError: 404
    - ValidationError: 422
    - BusinessError: 400
    - ExternalServiceError: 502
    - 其他 AppException: 400
    """
    # 根据异常类型确定 HTTP 状态码
    if isinstance(exc, NotFoundError):
        status_code = 404
    elif isinstance(exc, ValidationError):
        status_code = 422
    elif isinstance(exc, BusinessError):
        status_code = 400
    elif isinstance(exc, ExternalServiceError):
        status_code = 502
        # 记录外部服务错误日志
        logger.error(f"外部服务错误: {exc.message}")
    else:
        status_code = 400

    return _error_response(status_code, exc.code, exc.message)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理请求参数验证异常（Pydantic/FastAPI 验证错误）"""
    errors = exc.errors()
    if errors:
        # 提取第一个错误的详细信息
        first_error = errors[0]
        field = ".".join(str(loc) for loc in first_error.get("loc", []))
        msg = first_error.get("msg", "参数验证失败")
        message = f"{field}: {msg}" if field else msg
    else:
        message = "参数验证失败"

    return _error_response(422, "VALIDATION_ERROR", message)


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """处理 ValueError 异常

    将 ValueError 视为业务验证错误，返回 400 状态码。
    """
    return _error_response(400, "BAD_REQUEST", str(exc))


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理未捕获的异常

    记录错误日志并返回通用错误响应。
    """
    # 记录未预期的异常
    logger.exception(f"未处理的异常: {type(exc).__name__}: {exc}")

    return _error_response(500, "INTERNAL_ERROR", "服务器内部错误")
