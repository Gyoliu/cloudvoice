from core import config


def test_provider_mode_defaults_to_original(monkeypatch):
    """未配置启动模式时必须优先使用原有稳定链路。"""
    monkeypatch.delenv("AI_PROVIDER_MODE", raising=False)

    assert config.get_ai_provider_mode() is config.AIProviderMode.ORIGINAL


def test_provider_mode_supports_explicit_aggregator(monkeypatch):
    """显式选择 aggregator 时才允许使用聚合平台。"""
    monkeypatch.setenv("AI_PROVIDER_MODE", "aggregator")

    assert config.get_ai_provider_mode() is config.AIProviderMode.AGGREGATOR


def test_provider_mode_is_locked_for_process_lifetime(monkeypatch):
    """进程启动后修改环境变量不能改变已经锁定的提供方常量。"""
    startup_mode = config.AI_PROVIDER_MODE
    replacement = (
        "aggregator"
        if startup_mode is config.AIProviderMode.ORIGINAL
        else "original"
    )
    monkeypatch.setenv("AI_PROVIDER_MODE", replacement)

    assert config.AI_PROVIDER_MODE is startup_mode
    assert config.get_ai_provider_mode().value == replacement


def test_provider_mode_rejects_unknown_value(monkeypatch):
    """非法模式应在启动配置解析阶段失败，避免静默选错提供方。"""
    monkeypatch.setenv("AI_PROVIDER_MODE", "automatic")

    try:
        config.get_ai_provider_mode()
    except ValueError as exc:
        assert "original, aggregator" in str(exc)
    else:
        raise AssertionError("非法 AI_PROVIDER_MODE 不应被接受")


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


def test_aggregator_audio_fast_mode_is_normalized_to_supported_auto(monkeypatch):
    """聚合平台语音端点暂不接受 auto:fast，应无额外失败地使用 auto。"""
    monkeypatch.setattr(config, "AI_PROVIDER_MODE", config.AIProviderMode.AGGREGATOR)
    monkeypatch.setenv("LLM_AGGREGATOR_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("LLM_AGGREGATOR_API_KEY", "test-key")
    monkeypatch.setenv("LLM_AGGREGATOR_TTS_MODEL", "auto:fast")

    settings = config.get_llm_aggregator_settings()

    assert settings.available is True
    assert settings.base_url == "https://example.com/v1"
    assert settings.tts_model == "auto"


def test_original_mode_does_not_enable_configured_aggregator(monkeypatch):
    """即使保留聚合平台凭证，original 模式也不能自动切入聚合平台。"""
    monkeypatch.setattr(config, "AI_PROVIDER_MODE", config.AIProviderMode.ORIGINAL)
    monkeypatch.setenv("LLM_AGGREGATOR_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_AGGREGATOR_API_KEY", "test-key")

    assert config.get_llm_aggregator_settings().available is False
