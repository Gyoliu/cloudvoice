from core import config


def test_http_options_use_interactive_retry_defaults(monkeypatch):
    """语音交互请求应使用有限重试，避免代理故障导致长时间等待。"""
    for name in (
        "GEMINI_HTTP_TIMEOUT_MS",
        "GEMINI_HTTP_RETRY_ATTEMPTS",
        "GEMINI_TRUST_ENV",
        "GEMINI_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)

    options = config.build_http_options()

    assert options.timeout == 30_000
    assert options.retry_options.attempts == 3
    assert options.client_args == {"trust_env": True}
    assert options.async_client_args == {"trust_env": True}


def test_http_options_support_explicit_proxy(monkeypatch):
    """显式 Gemini 代理应同时应用于同步和异步客户端。"""
    monkeypatch.setenv("GEMINI_TRUST_ENV", "false")
    monkeypatch.setenv("GEMINI_PROXY", "http://127.0.0.1:7890")

    options = config.build_http_options()

    expected = {"trust_env": False, "proxy": "http://127.0.0.1:7890"}
    assert options.client_args == expected
    assert options.async_client_args == expected
