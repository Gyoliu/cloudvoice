import io
import wave
from types import SimpleNamespace

from services import tts_service


class FakeModels:
    """返回固定裸 PCM 的 Gemini 模型替身。"""

    def __init__(self, pcm_data: bytes):
        self.pcm_data = pcm_data

    def generate_content(self, **_kwargs):
        inline_data = SimpleNamespace(
            data=self.pcm_data,
            mime_type="audio/L16;codec=pcm;rate=16000",
        )
        part = SimpleNamespace(inline_data=inline_data)
        content = SimpleNamespace(parts=[part])
        candidate = SimpleNamespace(content=content)
        return SimpleNamespace(candidates=[candidate])


def test_generate_audio_wraps_pcm_as_wav(monkeypatch):
    """TTS 服务必须把模型返回的裸 PCM 封装成标准 WAV。"""
    pcm_data = b"\x00\x00\xff\x7f"
    fake_client = SimpleNamespace(models=FakeModels(pcm_data))
    monkeypatch.setattr(tts_service.clients, "gemini_client", fake_client)

    wav_data = tts_service.TTSService.generate_audio("测试", "Kore")

    with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == pcm_data
