import asyncio
import base64
import io
import wave
from types import SimpleNamespace

import pytest
from services import tts_service


def make_wav(pcm_data: bytes = b"\x00\x00\xff\x7f") -> bytes:
    """生成测试使用的标准 PCM WAV。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(pcm_data)
    return buffer.getvalue()


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


def test_generate_audio_prefers_llm_aggregator(monkeypatch):
    """aggregator 模式的普通 TTS 不应调用 Edge 或 Google。"""
    wav_content = make_wav()

    class FakeAggregator:
        settings = SimpleNamespace(available=True)

        async def synthesize_speech(self, text):
            assert text == "聚合平台测试"
            return wav_content

    monkeypatch.setattr(tts_service, "LLMAggregatorClient", FakeAggregator)
    monkeypatch.setattr(
        tts_service,
        "AI_PROVIDER_MODE",
        tts_service.AIProviderMode.AGGREGATOR,
    )

    async def unexpected_edge(_text):
        raise AssertionError("聚合平台成功时不应调用 Edge")

    monkeypatch.setattr(tts_service.TTSService, "_generate_edge_audio", unexpected_edge)
    result = asyncio.run(tts_service.TTSService.generate_audio("聚合平台测试", "Charon"))

    assert result.content == wav_content
    assert result.media_type == "audio/wav"
    assert result.provider == "llm-aggregator"


def test_start_audio_stream_prefers_aggregator_and_detects_real_wav(monkeypatch):
    """aggregator 模式应聚合首部字节并按魔数识别上游实际 WAV。"""
    wav_content = make_wav()

    class FakeAggregator:
        settings = SimpleNamespace(available=True)

        async def stream_speech(self, text):
            assert text == "流式测试"
            yield wav_content[:4]
            yield wav_content[4:20]
            yield wav_content[20:]

    monkeypatch.setattr(tts_service, "LLMAggregatorClient", FakeAggregator)
    monkeypatch.setattr(
        tts_service,
        "AI_PROVIDER_MODE",
        tts_service.AIProviderMode.AGGREGATOR,
    )

    async def run_test():
        started = await tts_service.TTSService.start_audio_stream("流式测试")
        remaining = b"".join([chunk async for chunk in started.stream])
        return started, remaining

    started, remaining = asyncio.run(run_test())

    assert started.provider == "llm-aggregator"
    assert started.media_type == "audio/wav"
    assert started.first_chunk + remaining == wav_content


def test_generate_audio_prefers_edge_tts_with_fixed_male_voice(monkeypatch):
    """默认 original 模式应使用固定的 Edge 中文男声并返回 MP3。"""
    class UnexpectedAggregator:
        def __init__(self):
            raise AssertionError("original 模式不应创建聚合平台客户端")

    monkeypatch.setattr(
        tts_service,
        "AI_PROVIDER_MODE",
        tts_service.AIProviderMode.ORIGINAL,
    )
    monkeypatch.setattr(tts_service, "LLMAggregatorClient", UnexpectedAggregator)
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
    monkeypatch.setattr(
        tts_service,
        "AI_PROVIDER_MODE",
        tts_service.AIProviderMode.ORIGINAL,
    )
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


def test_gemini_38_interactions_is_preferred(monkeypatch):
    """Google SDK 降级应优先使用正式 Gemini 3.8 Flash TTS。"""
    wav_content = make_wav()

    class FakeInteractions:
        def __init__(self):
            self.call = None

        def create(self, **kwargs):
            self.call = kwargs
            return SimpleNamespace(
                output_audio=SimpleNamespace(data=base64.b64encode(wav_content).decode())
            )

    interactions = FakeInteractions()
    fake_client = SimpleNamespace(interactions=interactions)
    monkeypatch.setattr(tts_service.clients, "gemini_client", fake_client)

    result = tts_service.TTSService._generate_gemini_audio("正式模型", "Charon")

    assert result == wav_content
    assert interactions.call["model"] == "gemini-3.8-flash-tts"
    assert interactions.call["generation_config"] == {
        "speech_config": [{"voice": "Charon"}]
    }


def test_aggregator_failure_does_not_fallback_to_original_mode(monkeypatch, caplog):
    """aggregator 模式失败后必须直接报错，不能跨模式调用 Edge 或 Gemini。"""

    class FailingAggregator:
        settings = SimpleNamespace(available=True)

        async def synthesize_speech(self, _text):
            raise RuntimeError("HTTP 429 code=insufficient_quota")

    async def fail_edge(_text):
        raise AssertionError("aggregator 模式不应调用 Edge")

    def fail_gemini(_text, _voice_name):
        raise AssertionError("aggregator 模式不应调用 Gemini")

    monkeypatch.setattr(tts_service, "LLMAggregatorClient", FailingAggregator)
    monkeypatch.setattr(
        tts_service,
        "AI_PROVIDER_MODE",
        tts_service.AIProviderMode.AGGREGATOR,
    )
    monkeypatch.setattr(tts_service.TTSService, "_generate_edge_audio", fail_edge)
    monkeypatch.setattr(tts_service.TTSService, "_generate_gemini_audio", fail_gemini)

    with caplog.at_level("WARNING"), pytest.raises(tts_service.TTSServiceError):
        asyncio.run(tts_service.TTSService.generate_audio("失败测试", "Charon"))

    log_text = caplog.text
    assert "聚合平台模式 TTS 调用失败" in log_text
    assert "HTTP 429 code=insufficient_quota" in log_text
    assert "切换 Microsoft Edge" not in log_text


def test_aggregator_stream_failure_does_not_fallback_to_edge(monkeypatch):
    """aggregator 流式请求首块失败时不能自动切换 Edge。"""

    class FailingAggregator:
        settings = SimpleNamespace(available=True)

        async def stream_speech(self, _text):
            raise RuntimeError("aggregator unavailable")
            yield b""  # pragma: no cover - 保持异步生成器接口

    async def unexpected_edge(_text):
        raise AssertionError("aggregator 模式不应启动 Edge 流")
        yield b""  # pragma: no cover - 保持异步生成器接口

    monkeypatch.setattr(tts_service, "LLMAggregatorClient", FailingAggregator)
    monkeypatch.setattr(
        tts_service,
        "AI_PROVIDER_MODE",
        tts_service.AIProviderMode.AGGREGATOR,
    )
    monkeypatch.setattr(tts_service.TTSService, "stream_edge_audio", unexpected_edge)

    with pytest.raises(tts_service.TTSServiceError):
        asyncio.run(tts_service.TTSService.start_audio_stream("失败测试"))
