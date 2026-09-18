import asyncio

from httpx import ASGITransport, AsyncClient
from main import app
from services.tts_service import GeneratedAudio, TTSService

TEST_API_ACCESS_TOKEN = "test-api-token"


def request(method: str, path: str, *, auth: bool = True, **kwargs):
    """通过 HTTPX ASGI transport 调用 FastAPI，避免启动真实网络服务。"""
    headers = dict(kwargs.pop("headers", {}) or {})
    if auth:
        headers.setdefault("Authorization", f"Bearer {TEST_API_ACCESS_TOKEN}")

    async def send_request():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, headers=headers, **kwargs)

    return asyncio.run(send_request())


def test_tts_rejects_unknown_voice():
    """未知音色应由 DTO 校验拒绝。"""
    response = request("POST", "/api/tts", json={"text": "测试", "voice_name": "Unknown"})
    assert response.status_code == 422


def test_tts_hides_internal_service_error(monkeypatch):
    """上游异常详情不应透传给 API 调用方。"""

    async def raise_internal_error(_text, _voice_name):
        raise RuntimeError("sensitive upstream detail")

    monkeypatch.setattr(TTSService, "generate_audio", raise_internal_error)
    response = request("POST", "/api/tts", json={"text": "测试"})

    assert response.status_code == 502
    assert response.json() == {"detail": "语音生成服务暂时不可用"}
    assert "sensitive upstream detail" not in response.text


def test_tts_uses_generated_audio_media_type_and_provider(monkeypatch):
    """TTS 路由应返回实际编码类型，并标识最终使用的提供方。"""

    async def generate_edge_audio(_text, _voice_name):
        return GeneratedAudio(
            content=b"mp3-audio",
            media_type="audio/mpeg",
            provider="microsoft-edge",
        )

    monkeypatch.setattr(TTSService, "generate_audio", generate_edge_audio)
    response = request("POST", "/api/tts", json={"text": "测试"})

    assert response.status_code == 200
    assert response.content == b"mp3-audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-tts-provider"] == "microsoft-edge"


def test_edge_tts_stream_returns_mp3_chunks_without_fallback(monkeypatch):
    """流式接口应仅透传 Edge MP3 分片，并明确禁止代理缓冲。"""

    async def generate_stream(_text):
        yield b"mp3-first"
        yield b"mp3-second"

    async def unexpected_fallback(_text, _voice_name):
        raise AssertionError("流式接口不应调用普通 TTS 降级链路")

    monkeypatch.setattr(TTSService, "stream_edge_audio", generate_stream)
    monkeypatch.setattr(TTSService, "generate_audio", unexpected_fallback)
    response = request("POST", "/api/tts/stream", json={"text": "测试"})

    assert response.status_code == 200
    assert response.content == b"mp3-firstmp3-second"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-tts-provider"] == "microsoft-edge"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-store"


def test_edge_tts_stream_returns_502_before_first_chunk(monkeypatch):
    """Edge 在首个分片前失败时应返回通用错误且不尝试 Google。"""

    async def fail_before_first_chunk(_text):
        if False:
            yield b"unreachable"
        raise ConnectionError("sensitive edge detail")

    monkeypatch.setattr(TTSService, "stream_edge_audio", fail_before_first_chunk)
    response = request("POST", "/api/tts/stream", json={"text": "测试"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Edge 流式语音服务暂时不可用"}
    assert "sensitive edge detail" not in response.text


def test_stt_upload_rejects_non_audio_file():
    """非音频文件应在调用 Gemini 前返回 415。"""
    response = request(
        "POST",
        "/api/stt/upload",
        files={"file": ("note.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 415


def test_frontend_index_is_served_from_same_origin():
    """同源部署时应由 FastAPI 直接提供前端首页。"""
    response = request("GET", "/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Google Voice" in response.text


def test_frontend_assets_are_served():
    """前端静态资源应可从同一服务访问。"""
    response = request("GET", "/app.js")
    assert response.status_code == 200
    assert "API_BASE" in response.text


def test_api_rejects_missing_token():
    """缺少 Token 的 /api 请求应返回 401。"""
    response = request("POST", "/api/tts", auth=False, json={"text": "测试"})
    assert response.status_code == 401
    assert response.json()["detail"] == "未授权：请提供有效的 API Token"


def test_api_rejects_invalid_token():
    """错误 Token 应返回 401。"""
    response = request(
        "POST",
        "/api/tts",
        auth=False,
        headers={"Authorization": "Bearer wrong-token"},
        json={"text": "测试"},
    )
    assert response.status_code == 401


def test_api_accepts_x_api_token_header(monkeypatch):
    """兼容 X-API-Token 头。"""

    async def generate_edge_audio(_text, _voice_name):
        return GeneratedAudio(
            content=b"mp3-audio",
            media_type="audio/mpeg",
            provider="microsoft-edge",
        )

    monkeypatch.setattr(TTSService, "generate_audio", generate_edge_audio)
    response = request(
        "POST",
        "/api/tts",
        auth=False,
        headers={"X-API-Token": TEST_API_ACCESS_TOKEN},
        json={"text": "测试"},
    )
    assert response.status_code == 200
