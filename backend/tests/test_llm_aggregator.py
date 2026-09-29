import asyncio
import json

import httpx
import pytest
from clients.llm_aggregator import LLMAggregatorClient, LLMAggregatorError
from core.config import LLMAggregatorSettings


def make_settings(**overrides) -> LLMAggregatorSettings:
    """构造不包含真实凭证的聚合平台测试配置。"""
    values = {
        "enabled": True,
        "base_url": "https://aggregator.test/v1",
        "api_key": "test-key",
        "tts_model": "gemini-3.8-flash-tts",
        "tts_voice": "zh-CN-YunxiNeural",
        "stt_model": "auto:fast",
        "connect_timeout_seconds": 1.0,
        "tts_timeout_seconds": 2.0,
        "stt_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return LLMAggregatorSettings(**values)


def test_tts_retries_auto_inside_aggregator_when_model_is_rejected():
    """显式音频模型未暴露时应重试平台 auto，而不是直接切换外部提供方。"""
    requested_models = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input"] == "测试"
        assert payload["text"] == payload["input"]
        requested_models.append(payload["model"])
        if payload["model"] != "auto":
            return httpx.Response(400, json={"error": {"message": "unknown audio model"}})
        return httpx.Response(200, content=b"RIFF\x00\x00\x00\x00WAVEaudio")

    client = LLMAggregatorClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    async def collect_audio():
        return b"".join([chunk async for chunk in client.stream_speech("测试")])

    content = asyncio.run(collect_audio())

    assert content.startswith(b"RIFF")
    assert requested_models == ["gemini-3.8-flash-tts", "auto"]


def test_stt_retries_auto_inside_aggregator_when_fast_mode_is_rejected(tmp_path):
    """STT 的 auto:fast 不受支持时应在聚合平台内改用 auto。"""
    audio_path = tmp_path / "test.wav"
    audio_path.write_bytes(b"RIFFtest")
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(400, json={"error": {"message": "unknown audio model"}})
        return httpx.Response(200, json={"text": "转录结果"})

    client = LLMAggregatorClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )

    transcript = asyncio.run(client.transcribe_audio(str(audio_path), "audio/wav"))

    assert transcript == "转录结果"
    assert call_count == 2


def test_tts_http_error_includes_safe_upstream_detail(monkeypatch):
    """聚合平台错误应保留状态、错误码和请求 ID，同时清除回显的密钥。"""
    monkeypatch.setenv("LLM_AGGREGATOR_API_KEY", "super-secret-api-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "type": "quota_error",
                    "code": "insufficient_quota",
                    "message": "Bearer super-secret-api-key has no quota",
                }
            },
            headers={"x-request-id": "request-123", "x-provider": "example-provider"},
        )

    client = LLMAggregatorClient(
        make_settings(tts_model="auto"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LLMAggregatorError) as captured:
        asyncio.run(client.synthesize_speech("测试"))

    detail = str(captured.value)
    assert "HTTP 429" in detail
    assert "requested_model=auto" in detail
    assert "code=insufficient_quota" in detail
    assert "x-request-id=request-123" in detail
    assert "x-provider=example-provider" in detail
    assert "super-secret-api-key" not in detail
    assert "[REDACTED]" in detail
