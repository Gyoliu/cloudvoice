import logging
import re

from core.audio import pcm_to_wav
from core.config import clients
from google.genai import types

logger = logging.getLogger(__name__)
DEFAULT_TTS_SAMPLE_RATE = 24_000


class TTSServiceError(RuntimeError):
    """TTS 服务调用或响应解析失败。"""


class TTSService:
    @staticmethod
    def generate_audio(text: str, voice_name: str) -> bytes:
        """
        调用 Gemini (原生多模态) 将文字转为语音。
        优先使用 gemini-3.1-flash-tts-preview，失败时降级至备用 TTS 模型。
        """
        if not clients.gemini_client:
            raise TTSServiceError("Gemini 客户端未初始化")

        prompt = (
            "请你一字不差地用极其自然的语音朗读以下这段文字，"
            f"绝对不要添加任何额外的词语、前缀、解释或对话：\n\n「{text}」"
        )
        config = types.GenerateContentConfig(
            temperature=0.2,
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
        )

        try:
            # 尝试首选模型
            response = clients.gemini_client.models.generate_content(
                model="gemini-3.1-flash-tts-preview", contents=prompt, config=config
            )
        except Exception:
            logger.warning(
                "TTS 首选模型 gemini-3.1-flash-tts-preview 调用失败，切换备用模型",
                exc_info=True,
            )
            # 降级备用模型
            try:
                response = clients.gemini_client.models.generate_content(
                    model="gemini-2.5-flash-preview-tts",
                    contents=prompt,
                    config=config,
                )
            except Exception as exc:
                raise TTSServiceError("Gemini TTS 模型调用失败") from exc

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
