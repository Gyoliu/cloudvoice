import asyncio
from types import SimpleNamespace

import pytest
from services import stt_service


class FakeFiles:
    """记录远端文件删除行为的 Files API 替身。"""

    def __init__(self):
        self.deleted_names = []

    def upload(self, **_kwargs):
        return SimpleNamespace(name="files/test-audio")

    def delete(self, name):
        self.deleted_names.append(name)


class FailingModels:
    """模拟主备模型均失败。"""

    def generate_content(self, **_kwargs):
        raise RuntimeError("upstream failed")


class SuccessfulModels:
    """记录模型配置并返回固定转录结果。"""

    def __init__(self):
        self.config = None

    def generate_content(self, **kwargs):
        self.config = kwargs["config"]
        return SimpleNamespace(text="转录结果")


def test_transcribe_file_deletes_remote_file_when_models_fail(monkeypatch):
    """模型失败时也必须删除 Gemini 远端临时音频。"""
    monkeypatch.setattr(
        stt_service,
        "AI_PROVIDER_MODE",
        stt_service.AIProviderMode.ORIGINAL,
    )
    fake_files = FakeFiles()
    fake_client = SimpleNamespace(files=fake_files, models=FailingModels())
    monkeypatch.setattr(stt_service.clients, "gemini_client", fake_client)

    with pytest.raises(stt_service.STTServiceError):
        asyncio.run(stt_service.STTService.transcribe_file("test.wav", "audio/wav"))

    assert fake_files.deleted_names == ["files/test-audio"]


def test_transcribe_file_disables_automatic_function_calling(monkeypatch):
    """普通音频转录不应启用 SDK 的自动函数调用。"""
    monkeypatch.setattr(
        stt_service,
        "AI_PROVIDER_MODE",
        stt_service.AIProviderMode.ORIGINAL,
    )
    fake_files = FakeFiles()
    fake_models = SuccessfulModels()
    fake_client = SimpleNamespace(files=fake_files, models=fake_models)
    monkeypatch.setattr(stt_service.clients, "gemini_client", fake_client)

    result = asyncio.run(stt_service.STTService.transcribe_file("test.wav", "audio/wav"))

    assert result == "转录结果"
    assert fake_models.config.automatic_function_calling.disable is True


def test_transcribe_file_prefers_llm_aggregator(monkeypatch):
    """aggregator 模式成功时不应上传音频到 Gemini SDK。"""

    class FakeAggregator:
        settings = SimpleNamespace(available=True)

        async def transcribe_audio(self, file_path, mime_type):
            assert file_path == "test.wav"
            assert mime_type == "audio/wav"
            return "聚合平台转录结果"

    monkeypatch.setattr(stt_service, "LLMAggregatorClient", FakeAggregator)
    monkeypatch.setattr(
        stt_service,
        "AI_PROVIDER_MODE",
        stt_service.AIProviderMode.AGGREGATOR,
    )
    monkeypatch.setattr(stt_service.clients, "gemini_client", None)

    result = asyncio.run(stt_service.STTService.transcribe_file("test.wav", "audio/wav"))

    assert result == "聚合平台转录结果"


def test_aggregator_stt_failure_does_not_fallback_to_gemini(monkeypatch):
    """aggregator 模式失败时直接报错，不进入原有 Gemini SDK 链路。"""

    class FailingAggregator:
        async def transcribe_audio(self, _file_path, _mime_type):
            raise RuntimeError("aggregator unavailable")

    def unexpected_gemini(_file_path, _mime_type):
        raise AssertionError("aggregator 模式不应调用 Gemini")

    monkeypatch.setattr(stt_service, "LLMAggregatorClient", FailingAggregator)
    monkeypatch.setattr(
        stt_service,
        "AI_PROVIDER_MODE",
        stt_service.AIProviderMode.AGGREGATOR,
    )
    monkeypatch.setattr(
        stt_service.STTService,
        "_transcribe_with_gemini",
        unexpected_gemini,
    )

    with pytest.raises(stt_service.STTServiceError):
        asyncio.run(stt_service.STTService.transcribe_file("test.wav", "audio/wav"))
