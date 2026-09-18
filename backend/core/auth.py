"""API 访问 Token 校验。"""

from __future__ import annotations

import os
import secrets

from fastapi import WebSocket, WebSocketException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp


def get_api_access_token() -> str:
    """读取服务端访问 Token；未配置时返回空字符串。"""
    return os.environ.get("API_ACCESS_TOKEN", "").strip()


def extract_http_token(request: Request) -> str | None:
    """从 Authorization Bearer 或 X-API-Token 提取客户端 Token。"""
    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token

    header_token = request.headers.get("X-API-Token", "").strip()
    return header_token or None


def tokens_match(provided: str | None, expected: str) -> bool:
    """常量时间比较，避免空 Token 误通过。"""
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided, expected)


def unauthorized_response() -> JSONResponse:
    return JSONResponse({"detail": "未授权：请提供有效的 API Token"}, status_code=401)


def misconfigured_response() -> JSONResponse:
    return JSONResponse(
        {"detail": "服务未配置 API_ACCESS_TOKEN"},
        status_code=503,
    )


class ApiTokenMiddleware(BaseHTTPMiddleware):
    """保护所有 /api HTTP 路由；静态页与文档路径放行。"""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
            return await call_next(request)

        expected = get_api_access_token()
        if not expected:
            return misconfigured_response()

        if not tokens_match(extract_http_token(request), expected):
            return unauthorized_response()

        return await call_next(request)


def assert_websocket_api_token(websocket: WebSocket) -> None:
    """WebSocket 通过 query `token` 校验；失败时拒绝连接。"""
    expected = get_api_access_token()
    provided = websocket.query_params.get("token")
    if not expected:
        raise WebSocketException(
            code=status.WS_1011_INTERNAL_ERROR,
            reason="API token missing",
        )
    if not tokens_match(provided, expected):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Unauthorized",
        )
