import logging

from core.config import clients
from google.genai import types

logger = logging.getLogger(__name__)


class STTServiceError(RuntimeError):
    """STT 服务调用或响应解析失败。"""


class STTService:
    @staticmethod
    def transcribe_file(file_path: str, mime_type: str | None = None) -> str:
        """
        调用 Gemini 解析本地音频文件为文本
        优先使用 gemini-3.6-flash，如果失败降级至 gemini-3.5-flash
        """
        if not clients.gemini_client:
            raise STTServiceError("Gemini 客户端未初始化")

        audio_file = None
        try:
            # 上传到云端
            upload_config = types.UploadFileConfig(mime_type=mime_type) if mime_type else None
            audio_file = clients.gemini_client.files.upload(
                file=file_path,
                config=upload_config,
            )
            prompt = (
                "你是一个精准的语音转录助手。请将这段音频中的所有语音完全、准确地"
                "转录为文字，不要添加任何额外的解释或对话，只输出转录的文本："
            )

            try:
                # 尝试首选模型
                response = clients.gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=[prompt, audio_file],
                    config=types.GenerateContentConfig(temperature=0.0),
                )
            except Exception:
                logger.warning(
                    "STT 首选模型 gemini-3.6-flash 调用失败，切换备用模型",
                    exc_info=True,
                )
                # 降级备用模型
                response = clients.gemini_client.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=[prompt, audio_file],
                    config=types.GenerateContentConfig(temperature=0.0),
                )
            transcript = (response.text or "").strip()
            if not transcript:
                raise STTServiceError("Gemini STT 模型未返回转录文本")
            return transcript
        except STTServiceError:
            raise
        except Exception as exc:
            raise STTServiceError("Gemini STT 调用失败") from exc
        finally:
            # 无论模型调用成功与否，都尝试删除远端临时音频。
            if audio_file and getattr(audio_file, "name", None):
                try:
                    clients.gemini_client.files.delete(name=audio_file.name)
                except Exception:
                    logger.exception("删除 Gemini 远端临时音频失败: %s", audio_file.name)
