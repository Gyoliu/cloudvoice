import json
import logging
import os
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from core.error_logging import safe_exception_chain

logger = logging.getLogger("ai.observability")
request_logger = logging.getLogger("request.observability")
_request_id_context: ContextVar[str | None] = ContextVar(
    "ai_request_id",
    default=None,
)


def bind_request_id(request_id: str | None = None) -> Token[str | None]:
    """为当前 HTTP/WebSocket 异步上下文绑定不可伪造的服务端请求 ID。"""
    return _request_id_context.set(request_id or uuid.uuid4().hex)


def reset_request_id(token: Token[str | None]) -> None:
    """请求结束后恢复上下文，避免连接复用时串联到下一次请求。"""
    _request_id_context.reset(token)


def current_request_id() -> str | None:
    """返回当前请求 ID；直接调用服务层时可能为空。"""
    return _request_id_context.get()


def _read_log_text_enabled() -> bool:
    """读取文本内容日志开关；默认按产品要求记录 AI 文本输入输出。"""
    raw_value = os.environ.get("AI_LOG_TEXT_CONTENT", "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _read_text_limit() -> int:
    """限制单个文本字段的日志长度；0 表示不截断。"""
    raw_value = os.environ.get("AI_LOG_TEXT_MAX_LENGTH", "12000").strip()
    try:
        return max(0, int(raw_value))
    except ValueError:
        return 12_000


def text_content(text: str) -> dict[str, Any]:
    """构建可观测文本；禁用内容日志时只保留字符数。"""
    result: dict[str, Any] = {"type": "text", "characters": len(text)}
    if not _read_log_text_enabled():
        result["content"] = "<text logging disabled>"
        return result

    limit = _read_text_limit()
    if limit and len(text) > limit:
        result.update(
            {
                "content": text[:limit],
                "truncated": True,
                "logged_characters": limit,
            }
        )
        return result

    result["content"] = text
    return result


def binary_content(
    content_type: str,
    *,
    byte_count: int | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """只记录文件/音频元数据，明确禁止把二进制内容写入日志。"""
    result: dict[str, Any] = {
        "type": "binary_stream" if stream else "binary",
        "content": "<binary omitted>",
        "content_type": content_type,
    }
    if byte_count is not None:
        result["bytes"] = byte_count
    return result


@dataclass
class AICallObservation:
    """记录一次 AI 提供方调用的请求、增量输出、结果、耗时和错误。"""

    operation: str
    provider: str
    model: str
    input_data: dict[str, Any]
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.monotonic)
    completed: bool = False

    @classmethod
    def start(
        cls,
        *,
        operation: str,
        provider: str,
        model: str,
        input_data: dict[str, Any],
        request_id: str | None = None,
    ) -> "AICallObservation":
        observation = cls(
            operation=operation,
            provider=provider,
            model=model,
            input_data=input_data,
            request_id=request_id or current_request_id() or uuid.uuid4().hex,
        )
        observation._log("request", input=input_data)
        return observation

    def partial(self, output_data: dict[str, Any]) -> None:
        """记录 Live API 等调用的增量文本输出。"""
        if not self.completed:
            self._log("partial_response", output=output_data)

    def success(self, output_data: dict[str, Any]) -> None:
        """记录成功结果；二进制输出只能传入 binary_content 元数据。"""
        if self.completed:
            return
        self.completed = True
        self._log("response", status="success", output=output_data)

    def failure(self, error: BaseException) -> None:
        """记录脱敏异常链，不输出认证头或 API Key。"""
        if self.completed:
            return
        self.completed = True
        self._log(
            "response",
            level=logging.ERROR,
            status="error",
            error=safe_exception_chain(error),
        )

    def cancelled(self, output_data: dict[str, Any] | None = None) -> None:
        """记录调用方提前关闭流的状态。"""
        if self.completed:
            return
        self.completed = True
        self._log("response", status="cancelled", output=output_data or {})

    def _log(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        payload = {
            "event": event,
            "request_id": self.request_id,
            "call_id": self.call_id,
            "operation": self.operation,
            "provider": self.provider,
            "model": self.model,
            "duration_ms": round((time.monotonic() - self.started_at) * 1000, 2),
            **fields,
        }
        logger.log(level, "AI_CALL %s", json.dumps(payload, ensure_ascii=False))


@dataclass
class RequestObservation:
    """记录不含查询参数和请求体的 API 生命周期，关联同请求内的 AI_CALL。"""

    method: str
    path: str
    transport: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.monotonic)
    completed: bool = False

    @classmethod
    def start(cls, *, method: str, path: str, transport: str) -> "RequestObservation":
        observation = cls(method=method, path=path, transport=transport)
        observation._log("request")
        return observation

    def success(self, status: int | str) -> None:
        if self.completed:
            return
        self.completed = True
        self._log("response", status=status)

    def failure(self, error: BaseException) -> None:
        if self.completed:
            return
        self.completed = True
        self._log(
            "response",
            level=logging.ERROR,
            status="error",
            error=safe_exception_chain(error),
        )

    def _log(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        payload = {
            "event": event,
            "request_id": self.request_id,
            "method": self.method,
            "path": self.path,
            "transport": self.transport,
            "duration_ms": round((time.monotonic() - self.started_at) * 1000, 2),
            **fields,
        }
        request_logger.log(level, "API_REQUEST %s", json.dumps(payload, ensure_ascii=False))
