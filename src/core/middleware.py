# src/core/middleware.py
"""中间件：认证 & 本地访问限制"""

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.app_context import get_app_context
from src.core.context import set_current_token
from src.core.exceptions import ExternalServiceError
from src.schemas.auth import UserInfo

# 调试用测试用户
DEBUG_USER = UserInfo(
    user_id=1,
    username="debug_user",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件

    拦截请求进行 Token 校验，将用户信息注入 request.state。
    CloudClient 从 AppContext 获取，实现连接池复用。
    """

    def __init__(
        self,
        app,
        whitelist: list[str],
        enabled: bool = True,
    ):
        super().__init__(app)
        self.whitelist = whitelist
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        # 白名单路径跳过认证
        if self._is_whitelisted(request.url.path):
            return await call_next(request)

        # 全局激活门禁：未激活时只允许白名单访问
        if not self._is_activated(request):
            return JSONResponse(
                status_code=403,
                content={
                    "code": 403,
                    "msg": "设备未激活，请先激活设备",
                    "data": None,
                    "error_code": "DEVICE_NOT_ACTIVATED",
                },
            )

        # 认证禁用时，注入调试用户
        if not self.enabled:
            request.state.user = DEBUG_USER
            return await call_next(request)

        # 提取 Token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "code": 401,
                    "msg": "缺少认证令牌",
                    "data": None,
                    "error_code": "UNAUTHORIZED",
                },
            )

        token = auth_header[7:]  # 去掉 "Bearer " 前缀

        services = get_app_context(request.app).services
        cloud_client = services.cloud_client
        if not cloud_client:
            logger.error("CloudClient 未初始化")
            return JSONResponse(
                status_code=503,
                content={
                    "code": 503,
                    "msg": "认证服务未就绪",
                    "data": None,
                    "error_code": "SERVICE_UNAVAILABLE",
                },
            )

        try:
            user = await cloud_client.verify_token(token)
        except ExternalServiceError as e:
            logger.error(f"认证服务异常: {e}")
            return JSONResponse(
                status_code=503,
                content={
                    "code": 503,
                    "msg": "认证服务暂时不可用",
                    "data": None,
                    "error_code": "SERVICE_UNAVAILABLE",
                },
            )

        if not user:
            return JSONResponse(
                status_code=401,
                content={
                    "code": 401,
                    "msg": "无效的认证令牌或用户被禁用",
                    "data": None,
                    "error_code": "UNAUTHORIZED",
                },
            )

        # 将用户信息注入 request.state
        request.state.user = user
        context = get_app_context(request.app)
        context.runtime.update_authorization(auth_header)

        # 设置 token 到上下文（供服务层使用）
        set_current_token(token)
        try:
            return await call_next(request)
        finally:
            set_current_token(None)

    def _is_whitelisted(self, path: str) -> bool:
        """检查路径是否在白名单中

        对于根路径 "/" 使用精确匹配，其他路径使用前缀匹配。
        """
        for p in self.whitelist:
            if p == "/":
                if path == "/":
                    return True
            elif path.startswith(p):
                return True
        return False

    def _is_activated(self, request: Request) -> bool:
        """检查设备是否已激活

        默认策略：当应用未设置 device_status 时，视为已激活，避免影响非主应用场景。
        """
        try:
            runtime = get_app_context(request.app).runtime
        except RuntimeError:
            return True
        return runtime.device.is_activated


# ---------------------------------------------------------------------------
# 本地访问限制中间件
# ---------------------------------------------------------------------------

# 允许的本地 IP 地址
LOCAL_IPS = {"127.0.0.1", "::1", "localhost", "testclient"}


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """仅允许本地访问的中间件"""

    def __init__(self, app, protected_paths: list[str] | None = None):
        """初始化中间件

        Args:
            app: ASGI 应用
            protected_paths: 受保护的路径前缀列表，默认 ["/debug"]
        """
        super().__init__(app)
        self.protected_paths = protected_paths or ["/debug"]

    async def dispatch(self, request: Request, call_next):
        """处理请求

        检查是否是受保护路径，如果是则验证客户端 IP。
        """
        if any(request.url.path.startswith(p) for p in self.protected_paths):
            client_host = request.client.host if request.client else None
            if client_host not in LOCAL_IPS:
                return JSONResponse(status_code=403, content={"detail": "仅允许本地访问"})
        return await call_next(request)
