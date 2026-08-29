import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import edge_tts
from core.audio import pcm_to_wav
from core.config import clients
from google.genai import types

logger = logging.getLogger(__name__)
DEFAULT_TTS_SAMPLE_RATE = 24_000
EDGE_TTS_VOICE = "zh-CN-YunxiNeural"
EDGE_TTS_CONNECT_TIMEOUT_SECONDS = 5
EDGE_TTS_RECEIVE_TIMEOUT_SECONDS = 10
EDGE_TTS_TOTAL_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class GeneratedAudio:
    """TTS 生成结果及浏览器播放所需的响应元数据。"""

    # 实际音频二进制内容。
    content: bytes
    # 与音频编码一致的 HTTP Content-Type。
    media_type: str
    # 实际完成合成的服务提供方，用于日志和故障定位。
    provider: str


class TTSServiceError(RuntimeError):
    """TTS 服务调用或响应解析失败。"""


class TTSService:
    @staticmethod
    async def generate_audio(text: str, voice_name: str) -> GeneratedAudio:
        """
        优先使用 Microsoft Edge TTS，失败时自动降级至 Gemini TTS。

        Edge TTS 使用固定中文男声并返回 MP3；Gemini 降级路径返回 WAV。
        """
        try:
            return await TTSService._generate_edge_audio(text)
        except Exception as edge_error:
            logger.warning(
                "Microsoft Edge TTS 调用失败（%s），切换 Google Gemini TTS",
                type(edge_error).__name__,
            )

        try:
            content = await asyncio.to_thread(
                TTSService._generate_gemini_audio,
                text,
                voice_name,
            )
            return GeneratedAudio(
                content=content,
                media_type="audio/wav",
                provider="google-gemini",
            )
        except Exception as google_error:
            logger.exception("Microsoft Edge TTS 与 Google Gemini TTS 均调用失败")
            raise TTSServiceError("所有 TTS 服务均暂时不可用") from google_error

    @staticmethod
    async def _generate_edge_audio(text: str) -> GeneratedAudio:
        """使用固定中文男声调用 Edge TTS，并在限定时间内收集 MP3 分片。"""
        audio_parts: list[bytes] = []

        async with asyncio.timeout(EDGE_TTS_TOTAL_TIMEOUT_SECONDS):
            async for audio_chunk in TTSService.stream_edge_audio(text):
                audio_parts.append(audio_chunk)

        logger.info("Microsoft Edge TTS 音频生成成功，voice=%s", EDGE_TTS_VOICE)
        return GeneratedAudio(
            content=b"".join(audio_parts),
            media_type="audio/mpeg",
            provider="microsoft-edge",
        )

    @staticmethod
    async def stream_edge_audio(text: str) -> AsyncIterator[bytes]:
        """仅使用 Edge TTS 逐块生成 MP3；该方法不会触发 Gemini 降级。"""
        communicate = edge_tts.Communicate(
            text,
            EDGE_TTS_VOICE,
            connect_timeout=EDGE_TTS_CONNECT_TIMEOUT_SECONDS,
            receive_timeout=EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
        )
        audio_received = False
        edge_stream = communicate.stream()

        try:
            async for chunk in edge_stream:
                audio_data = chunk.get("data") if chunk.get("type") == "audio" else None
                if audio_data:
                    audio_received = True
                    # 直接向 HTTP 响应交付分片，避免等待完整音频生成后再播放。
                    yield audio_data
        except Exception as exc:
            logger.warning("Microsoft Edge TTS 流式生成失败（%s）", type(exc).__name__)
            raise TTSServiceError("Microsoft Edge TTS 流式生成失败") from exc
        finally:
            # 客户端停止播放时主动关闭 Edge SDK 的上游异步生成器。
            await edge_stream.aclose()

        if not audio_received:
            raise TTSServiceError("Microsoft Edge TTS 未返回音频数据")

    @staticmethod
    def _generate_gemini_audio(text: str, voice_name: str) -> bytes:
        """调用 Gemini 原生语音模型并将返回的裸 PCM 统一封装成 WAV。"""
        if not clients.gemini_client:
            raise TTSServiceError("Gemini 客户端未初始化")

        prompt = (
            "请你一字不差地用极其自然的语音朗读以下这段文字，"
            f"绝对不要添加任何额外的词语、前缀、解释或对话：\n\n「{text}」"
        )
        config = types.GenerateContentConfig(
            temperature=0.2,
            response_modalities=["AUDIO"],
            # TTS 不使用函数工具，关闭 SDK 默认 AFC，避免无意义的警告与循环。
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
        )

        # Google 降级路径仍按模型优先级尝试，尽量避免单个预览模型不可用。
        models_to_try = [
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts",
        ]

        last_error = None
        for model_name in models_to_try:
            try:
                logger.info("正在尝试使用 TTS 模型: %s", model_name)
                response = clients.gemini_client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
                break  # 成功则跳出循环
            except Exception as e:
                logger.warning("模型 %s 调用失败: %s", model_name, type(e).__name__)
                last_error = e
        else:
            logger.error("所有 Gemini TTS 模型均调用失败")
            raise TTSServiceError(f"TTS 请求全部失败。最后的错误: {last_error}")

        # Gemini TTS 通常返回裸 PCM；统一封装成浏览器可播放的 WAV。
        try:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                    audio_data = part.inline_data.data
                    mime_type = part.inline_data.mime_type.lower()
                    if audio_data.startswith(b"RIFF") and audio_data[8:12] == b"WAVE":
                        return audio_data

                    rate_match = re.search(r"rate=(\d+)", mime_type)
                    sample_rate = (
                        int(rate_match.group(1)) if rate_match else DEFAULT_TTS_SAMPLE_RATE
                    )
                    return pcm_to_wav(audio_data, sample_rate=sample_rate)
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise TTSServiceError("无法解析 Gemini TTS 音频响应") from exc

        raise TTSServiceError("Gemini TTS 模型未返回音频数据")
