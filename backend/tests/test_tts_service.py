import asyncio
import io
import wave
from types import SimpleNamespace

from services import tts_service


class FakeModels:
    """返回固定裸 PCM 的 Gemini 模型替身。"""

    def __init__(self, pcm_data: bytes):
        self.pcm_data = pcm_data
        self.config = None

    def generate_content(self, **kwargs):
        self.config = kwargs["config"]
        inline_data = SimpleNamespace(
            data=self.pcm_data,
            mime_type="audio/L16;codec=pcm;rate=16000",
        )
        part = SimpleNamespace(inline_data=inline_data)
        content = SimpleNamespace(parts=[part])
        candidate = SimpleNamespace(content=content)
        return SimpleNamespace(candidates=[candidate])


class FakeEdgeCommunicate:
    """返回固定 MP3 分片并记录 Edge TTS 调用参数。"""

    last_call = None
    stream_closed = False

    def __init__(self, text, voice, **kwargs):
        FakeEdgeCommunicate.stream_closed = False
        FakeEdgeCommunicate.last_call = {
            "text": text,
            "voice": voice,
            **kwargs,
        }

    async def stream(self):
        try:
            yield {"type": "audio", "data": b"mp3-part-1"}
            yield {"type": "WordBoundary", "text": "测试"}
            yield {"type": "audio", "data": b"mp3-part-2"}
        finally:
            FakeEdgeCommunicate.stream_closed = True


def test_generate_audio_prefers_edge_tts_with_fixed_male_voice(monkeypatch):
    """正常情况下应使用固定的 Edge 中文男声并返回 MP3。"""
    monkeypatch.setattr(tts_service.edge_tts, "Communicate", FakeEdgeCommunicate)

    result = asyncio.run(tts_service.TTSService.generate_audio("测试", "Kore"))

    assert result.content == b"mp3-part-1mp3-part-2"
    assert result.media_type == "audio/mpeg"
    assert result.provider == "microsoft-edge"
    assert FakeEdgeCommunicate.last_call["text"] == "测试"
    assert FakeEdgeCommunicate.last_call["voice"] == "zh-CN-YunxiNeural"


def test_stream_edge_audio_yields_only_audio_chunks(monkeypatch):
    """Edge-only 流式方法应按顺序交付 MP3 分片并忽略边界事件。"""
    monkeypatch.setattr(tts_service.edge_tts, "Communicate", FakeEdgeCommunicate)

    async def collect_chunks():
        return [
            chunk
            async for chunk in tts_service.TTSService.stream_edge_audio("流式测试")
        ]

    chunks = asyncio.run(collect_chunks())

    assert chunks == [b"mp3-part-1", b"mp3-part-2"]
    assert FakeEdgeCommunicate.last_call["voice"] == "zh-CN-YunxiNeural"


def test_stream_edge_audio_closes_upstream_when_consumer_stops(monkeypatch):
    """客户端提前停止时应关闭 Edge SDK 上游生成器，释放网络会话。"""
    monkeypatch.setattr(tts_service.edge_tts, "Communicate", FakeEdgeCommunicate)

    async def read_one_chunk_and_stop():
        stream = tts_service.TTSService.stream_edge_audio("提前停止测试")
        try:
            assert await anext(stream) == b"mp3-part-1"
        finally:
            await stream.aclose()

    asyncio.run(read_one_chunk_and_stop())

    assert FakeEdgeCommunicate.stream_closed is True


def test_generate_audio_falls_back_to_gemini_and_wraps_pcm(monkeypatch):
    """Edge TTS 失败时应调用 Gemini，并把模型返回的裸 PCM 封装成 WAV。"""
    pcm_data = b"\x00\x00\xff\x7f"
    fake_client = SimpleNamespace(models=FakeModels(pcm_data))
    monkeypatch.setattr(tts_service.clients, "gemini_client", fake_client)

    async def fail_edge(_text):
        raise ConnectionError("edge unavailable")

    monkeypatch.setattr(tts_service.TTSService, "_generate_edge_audio", fail_edge)
    result = asyncio.run(tts_service.TTSService.generate_audio("测试", "Charon"))

    assert result.media_type == "audio/wav"
    assert result.provider == "google-gemini"
    with wave.open(io.BytesIO(result.content), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == pcm_data

    assert fake_client.models.config.automatic_function_calling.disable is True
