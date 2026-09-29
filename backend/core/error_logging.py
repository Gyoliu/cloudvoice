import os
import re

_SECRET_ENV_NAMES = (
    "LLM_AGGREGATOR_API_KEY",
    "GEMINI_API_KEY",
    "API_ACCESS_TOKEN",
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_NAMED_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|api[-_]?key|access[-_]?token|token|credential)"
    r"(\s*[:=]\s*)"
    r"(?:bearer\s+)?"
    r"([^\s,;&}\]]+)"
)


def _redact_sensitive_values(message: str) -> str:
    """清除异常文本中的已知密钥和常见认证字段，避免诊断日志泄露凭证。"""
    redacted = message
    for name in _SECRET_ENV_NAMES:
        secret = os.environ.get(name, "")
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    return _NAMED_SECRET_PATTERN.sub(r"\1\2[REDACTED]", redacted)


def safe_exception_detail(error: BaseException, max_length: int = 800) -> str:
    """输出单个异常的脱敏摘要；不记录请求正文、音频或调用参数。"""
    message = " ".join(str(error).split()) or "无错误消息"
    detail = f"{type(error).__name__}: {_redact_sensitive_values(message)}"
    if len(detail) <= max_length:
        return detail
    return f"{detail[: max_length - 3]}..."


def safe_exception_chain(
    error: BaseException,
    *,
    max_depth: int = 4,
    max_length: int = 1_600,
) -> str:
    """沿异常因果链生成脱敏摘要，显示被业务异常包装的底层网络原因。"""
    chain: list[str] = []
    visited: set[int] = set()
    current: BaseException | None = error

    while current is not None and len(chain) < max_depth and id(current) not in visited:
        visited.add(id(current))
        chain.append(safe_exception_detail(current, max_length=max_length))
        current = current.__cause__ or current.__context__

    detail = " <- ".join(chain)
    if len(detail) <= max_length:
        return detail
    return f"{detail[: max_length - 3]}..."
