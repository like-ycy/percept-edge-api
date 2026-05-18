# src/core/exceptions.py
"""应用自定义异常层次结构

所有业务异常都继承自 AppException，提供统一的错误码和消息格式。
全局异常处理器会根据异常类型返回对应的 HTTP 状态码。
"""


class AppException(Exception):
    """应用基础异常

    所有业务异常的基类，提供统一的错误码和消息格式。

    Attributes:
        message: 错误描述信息
        code: 错误码，用于客户端识别错误类型
    """

    def __init__(self, message: str, code: str = "INTERNAL_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class NotFoundError(AppException):
    """资源不存在异常（HTTP 404）

    当请求的资源（如任务、文件等）不存在时抛出。

    Args:
        resource: 资源类型名称
        id: 资源标识符
    """

    def __init__(self, resource: str, id: str):
        super().__init__(f"{resource} {id} 不存在", "NOT_FOUND")


class ValidationError(AppException):
    """数据验证异常（HTTP 422）

    当输入数据不符合验证规则时抛出。

    Args:
        message: 验证失败的具体描述
    """

    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")


class BusinessError(AppException):
    """业务逻辑异常（HTTP 400）

    当业务规则不满足时抛出，如"采集已在进行中"等。

    Args:
        message: 业务错误描述
    """

    def __init__(self, message: str):
        super().__init__(message, "BUSINESS_ERROR")


class ExternalServiceError(AppException):
    """外部服务调用异常（HTTP 502）

    当调用外部服务（如 IAM、存储服务等）失败时抛出。

    Args:
        service: 外部服务名称
        message: 错误描述
    """

    def __init__(self, service: str, message: str):
        super().__init__(f"{service}: {message}", "EXTERNAL_SERVICE_ERROR")
