import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

PROJECT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_project_environment(env_file: Path = PROJECT_ENV_FILE) -> None:
    """以项目 backend/.env 为准，避免终端遗留变量覆盖当前项目的 Gemini 凭证。"""
    # 部署环境未提供该文件时不会改写现有环境变量，仍兼容容器/系统注入配置。
    load_dotenv(env_file, override=env_file.is_file())


load_project_environment()


logger = logging.getLogger(__name__)


def _read_positive_int(name: str, default: int) -> int:
    """读取正整数环境变量；配置非法时快速失败，避免静默使用错误值。"""
    raw_value = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


def _read_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量。"""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


def build_http_options() -> types.HttpOptions:
    """构建适合交互式语音请求的超时、重试及代理配置。"""
    timeout_ms = _read_positive_int("GEMINI_HTTP_TIMEOUT_MS", 30_000)
    retry_attempts = _read_positive_int("GEMINI_HTTP_RETRY_ATTEMPTS", 3)
    trust_env = _read_bool("GEMINI_TRUST_ENV", True)
    proxy_url = os.environ.get("GEMINI_PROXY", "").strip()

    client_args: dict[str, object] = {"trust_env": trust_env}
    if proxy_url:
        # 显式代理优先于系统代理，避免开发机代理切换造成连接池不稳定。
        client_args["proxy"] = proxy_url

    return types.HttpOptions(
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(
            attempts=retry_attempts,
            initial_delay=0.5,
            max_delay=2.0,
            exp_base=2.0,
            jitter=0.2,
        ),
        client_args=client_args,
        async_client_args=client_args.copy(),
    )


class GoogleClients:
    def __init__(self):
        self.gemini_client = None
        self._init_clients()

    def _init_clients(self):
        try:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("未找到 GEMINI_API_KEY 环境变量，请检查您的 .env 文件！")

            self.gemini_client = genai.Client(
                api_key=api_key,
                http_options=build_http_options(),
            )
            logger.info("Gemini SDK 客户端初始化成功")
        except Exception:
            logger.exception("Gemini SDK 客户端初始化失败，请检查 GEMINI_API_KEY")


# 单例模式，供全局调用
clients = GoogleClients()
