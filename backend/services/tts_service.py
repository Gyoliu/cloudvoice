import asyncio
import base64
import binascii
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import edge_tts
from clients.llm_aggregator import LLMAggregatorClient
from core.ai_observability import (
    AICallObservation,
    binary_content,
    text_content,
)
from core.audio import pcm_to_wav
from core.config import AI_PROVIDER_MODE, AIProviderMode, clients
from core.error_logging import safe_exception_chain
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


@dataclass
class StartedAudioStream:
    """已取得首个音频分片、可以安全发送 HTTP 响应头的流。"""

    # 已从上游读取并用于格式识别的首批音频字节。
    first_chunk: bytes
    # 首批字节之后的上游音频迭代器。
    stream: AsyncIterator[bytes]
    # 根据文件签名识别出的真实媒体类型。
    media_type: str
    # 实际使用的音频提供方。
    provider: str


class TTSServiceError(RuntimeError):
    """TTS 服务调用或响应解析失败。"""


class TTSService:
    @staticmethod
    async def generate_audio(text: str, voice_name: str) -> GeneratedAudio:
        """
        按启动配置选择聚合平台或原有 Edge → Gemini 稳定链路。

        两种模式互斥，单次请求失败时不会跨提供方模式自动切换。
        """
        if AI_PROVIDER_MODE is AIProviderMode.AGGREGATOR:
            aggregator = LLMAggregatorClient()
            try:
                content = await aggregator.synthesize_speech(text)
                return GeneratedAudio(
                    content=content,
                    media_type=TTSService.detect_audio_media_type(content),
                    provider="llm-aggregator",
                )
            except Exception as aggregator_error:
                logger.error(
                    "聚合平台模式 TTS 调用失败：%s",
                    safe_exception_chain(aggregator_error),
                    exc_info=True,
                )
                raise TTSServiceError("聚合平台 TTS 服务暂时不可用") from aggregator_error

        failures: list[tuple[str, BaseException]] = []
        edge_observation = AICallObservation.start(
            operation="tts",
            provider="microsoft-edge",
            model=EDGE_TTS_VOICE,
            input_data={"text": text_content(text), "voice": EDGE_TTS_VOICE},
        )
        try:
            edge_audio = await TTSService._generate_edge_audio(text)
            edge_observation.success(
                binary_content(
                    edge_audio.media_type,
                    byte_count=len(edge_audio.content),
                )
            )
            return edge_audio
        except Exception as edge_error:
            edge_observation.failure(edge_error)
            failures.append(("microsoft-edge", edge_error))
            logger.warning(
                "Microsoft Edge TTS 调用失败：%s；切换 Google Gemini TTS",
                safe_exception_chain(edge_error),
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
            failures.append(("google-gemini", google_error))
            failure_detail = " | ".join(
                f"{provider}=[{safe_exception_chain(error)}]"
                for provider, error in failures
            )
            logger.error(
                "原有 TTS 稳定链路全部调用失败：%s",
                failure_detail,
                exc_info=True,
            )
            raise TTSServiceError("所有 TTS 服务均暂时不可用") from google_error

    @staticmethod
    async def start_audio_stream(text: str) -> StartedAudioStream:
        """按启动配置启动聚合平台或原有 Edge 音频流，不跨模式降级。"""
        if AI_PROVIDER_MODE is AIProviderMode.AGGREGATOR:
            aggregator = LLMAggregatorClient()
            aggregator_stream = aggregator.stream_speech(text)
            try:
                first_chunk = await TTSService._read_probe(aggregator_stream)
                return StartedAudioStream(
                    first_chunk=first_chunk,
                    stream=aggregator_stream,
                    media_type=TTSService.detect_audio_media_type(first_chunk),
                    provider="llm-aggregator",
                )
            except Exception as aggregator_error:
                await aggregator_stream.aclose()
                logger.error(
                    "聚合平台模式流式 TTS 启动失败：%s",
                    safe_exception_chain(aggregator_error),
                    exc_info=True,
                )
                raise TTSServiceError(
                    "聚合平台流式 TTS 服务暂时不可用"
                ) from aggregator_error

        edge_observation = AICallObservation.start(
            operation="tts.stream",
            provider="microsoft-edge",
            model=EDGE_TTS_VOICE,
            input_data={"text": text_content(text), "voice": EDGE_TTS_VOICE},
        )
        edge_stream = TTSService.stream_edge_audio(text)
        try:
            first_chunk = await TTSService._read_probe(edge_stream)
            media_type = TTSService.detect_audio_media_type(first_chunk)
            return StartedAudioStream(
                first_chunk=first_chunk,
                stream=TTSService._observe_audio_stream(
                    edge_stream,
                    edge_observation,
                    initial_byte_count=len(first_chunk),
                    media_type=media_type,
                ),
                media_type=media_type,
                provider="microsoft-edge",
            )
        except Exception as edge_error:
            await edge_stream.aclose()
            edge_observation.failure(edge_error)
            logger.error(
                "原有模式 Microsoft Edge 流式 TTS 启动失败：%s",
                safe_exception_chain(edge_error),
                exc_info=True,
            )
            raise TTSServiceError("Microsoft Edge 流式 TTS 服务暂时不可用") from edge_error

    @staticmethod
    async def _observe_audio_stream(
        audio_stream: AsyncIterator[bytes],
        observation: AICallObservation,
        *,
        initial_byte_count: int,
        media_type: str,
    ) -> AsyncIterator[bytes]:
        """统计流式响应元数据；不记录或缓存任何音频二进制内容。"""
        byte_count = initial_byte_count
        try:
            async for chunk in audio_stream:
                byte_count += len(chunk)
                yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            observation.cancelled(
                binary_content(media_type, byte_count=byte_count, stream=True)
            )
            raise
        except Exception as exc:
            observation.failure(exc)
            raise
        else:
            observation.success(
                binary_content(media_type, byte_count=byte_count, stream=True)
            )
        finally:
            await audio_stream.aclose()

    @staticmethod
    async def _read_probe(audio_stream: AsyncIterator[bytes]) -> bytes:
        """读取足够的流首部字节用于可靠识别 WAV 或 MP3。"""
        probe = bytearray()
        while len(probe) < 12:
            try:
                probe.extend(await anext(audio_stream))
            except StopAsyncIteration:
                break
        if not probe:
            raise TTSServiceError("TTS 上游未返回音频数据")
        return bytes(probe)

    @staticmethod
    def detect_audio_media_type(content: bytes) -> str:
        """按音频魔数识别真实编码，避免依赖不准确的上游 Content-Type。"""
        if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WAVE":
            return "audio/wav"
        if content.startswith(b"ID3"):
            return "audio/mpeg"
        if len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0:
            return "audio/mpeg"
        raise TTSServiceError("TTS 上游返回了无法识别的音频格式")

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
            logger.warning(
                "Microsoft Edge TTS 流式生成失败：%s",
                safe_exception_chain(exc),
            )
            raise TTSServiceError("Microsoft Edge TTS 流式生成失败") from exc
        finally:
            # 客户端停止播放时主动关闭 Edge SDK 的上游异步生成器。
            await edge_stream.aclose()

        if not audio_received:
            raise TTSServiceError("Microsoft Edge TTS 未返回音频数据")

    @staticmethod
    def _generate_gemini_audio(text: str, voice_name: str) -> bytes:
        """优先调用 Gemini 3.8 TTS，失败后兼容旧版 generate_content 模型。"""
        if not clients.gemini_client:
            raise TTSServiceError("Gemini 客户端未初始化")

        interaction_observation = AICallObservation.start(
            operation="tts",
            provider="google-gemini",
            model="gemini-3.8-flash-tts",
            input_data={"text": text_content(text), "voice": voice_name},
        )
        try:
            interaction = clients.gemini_client.interactions.create(
                model="gemini-3.8-flash-tts",
                input=[
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                                "annotations": [
                                    {
                                        "type": "speech_metadata",
                                        "style": "自然、清晰、沉稳的普通话男声",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice_name}]},
            )
            output_audio = getattr(interaction, "output_audio", None)
            encoded_audio = getattr(output_audio, "data", None)
            if not encoded_audio:
                raise TTSServiceError("Gemini 3.8 TTS 未返回音频数据")
            try:
                audio_data = base64.b64decode(encoded_audio, validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise TTSServiceError("Gemini 3.8 TTS 音频解码失败") from exc
            if not audio_data:
                raise TTSServiceError("Gemini 3.8 TTS 返回了空音频")
            interaction_observation.success(
                binary_content("audio/wav", byte_count=len(audio_data))
            )
            logger.info("Gemini 3.8 TTS 音频生成成功，model=gemini-3.8-flash-tts")
            return audio_data
        except Exception as exc:
            interaction_observation.failure(exc)
            logger.warning(
                "Gemini 3.8 TTS 调用失败：%s；切换兼容模型",
                safe_exception_chain(exc),
            )

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

        # 旧模型路径继续保留，避免 Gemini 3.8 在地区或账号尚未开放时中断服务。
        models_to_try = [
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts",
        ]

        last_error = None
        successful_observation: AICallObservation | None = None
        for model_name in models_to_try:
            model_observation = AICallObservation.start(
                operation="tts",
                provider="google-gemini",
                model=model_name,
                input_data={"text": text_content(prompt), "voice": voice_name},
            )
            try:
                logger.info("正在尝试使用 TTS 模型: %s", model_name)
                response = clients.gemini_client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
                successful_observation = model_observation
                break  # 成功则跳出循环
            except Exception as e:
                model_observation.failure(e)
                logger.warning(
                    "Gemini TTS 模型 %s 调用失败：%s",
                    model_name,
                    safe_exception_chain(e),
                )
                last_error = e
        else:
            logger.error(
                "所有 Gemini TTS 模型均调用失败；最后错误：%s",
                safe_exception_chain(last_error) if last_error else "无错误对象",
            )
            raise TTSServiceError("所有 Gemini TTS 模型均调用失败") from last_error

        # Gemini TTS 通常返回裸 PCM；统一封装成浏览器可播放的 WAV。
        try:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
                    audio_data = part.inline_data.data
                    mime_type = part.inline_data.mime_type.lower()
                    if audio_data.startswith(b"RIFF") and audio_data[8:12] == b"WAVE":
                        if successful_observation:
                            successful_observation.success(
                                binary_content("audio/wav", byte_count=len(audio_data))
                            )
                        return audio_data

                    rate_match = re.search(r"rate=(\d+)", mime_type)
                    sample_rate = (
                        int(rate_match.group(1)) if rate_match else DEFAULT_TTS_SAMPLE_RATE
                    )
                    wav_audio = pcm_to_wav(audio_data, sample_rate=sample_rate)
                    if successful_observation:
                        successful_observation.success(
                            binary_content("audio/wav", byte_count=len(wav_audio))
                        )
                    return wav_audio
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            if successful_observation:
                successful_observation.failure(exc)
            raise TTSServiceError("无法解析 Gemini TTS 音频响应") from exc

        if successful_observation:
            successful_observation.failure(
                TTSServiceError("Gemini TTS 模型未返回音频数据")
            )
        raise TTSServiceError("Gemini TTS 模型未返回音频数据")
