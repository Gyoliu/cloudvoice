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


def test_project_env_file_overrides_stale_process_key(monkeypatch, tmp_path):
    """本地项目密钥应覆盖终端遗留值，确保 SDK 使用当前 backend/.env。"""
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=current-project-key\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "stale-shell-key")

    config.load_project_environment(env_file)

    assert config.os.environ["GEMINI_API_KEY"] == "current-project-key"


def test_missing_project_env_preserves_deployment_key(monkeypatch, tmp_path):
    """容器没有 backend/.env 时应继续使用平台注入的环境变量。"""
    monkeypatch.setenv("GEMINI_API_KEY", "deployment-key")

    config.load_project_environment(tmp_path / "missing.env")

    assert config.os.environ["GEMINI_API_KEY"] == "deployment-key"
