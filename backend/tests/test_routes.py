import asyncio

from httpx import ASGITransport, AsyncClient
from main import app
from services.tts_service import TTSService


def request(method: str, path: str, **kwargs):
    """通过 HTTPX ASGI transport 调用 FastAPI，避免启动真实网络服务。"""

    async def send_request():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send_request())


def test_tts_rejects_unknown_voice():
    """未知音色应由 DTO 校验拒绝。"""
    response = request("POST", "/api/tts", json={"text": "测试", "voice_name": "Unknown"})
    assert response.status_code == 422


def test_tts_hides_internal_service_error(monkeypatch):
    """上游异常详情不应透传给 API 调用方。"""

    def raise_internal_error(_text, _voice_name):
        raise RuntimeError("sensitive upstream detail")

    monkeypatch.setattr(TTSService, "generate_audio", raise_internal_error)
    response = request("POST", "/api/tts", json={"text": "测试"})

    assert response.status_code == 502
    assert response.json() == {"detail": "语音生成服务暂时不可用"}
    assert "sensitive upstream detail" not in response.text


def test_stt_upload_rejects_non_audio_file():
    """非音频文件应在调用 Gemini 前返回 415。"""
    response = request(
        "POST",
        "/api/stt/upload",
        files={"file": ("note.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 415
