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
                "你是一个精准的简体中文语音转录助手。这段音频默认使用普通话中文；"
                "除非音频中存在清晰、完整的外语表达，否则不要切换到其他语言或文字体系。"
                "请完整、准确地转录所有语音，不要添加解释或对话，只输出转录文本："
            )
            # 纯音频转录不使用工具，显式关闭 SDK 默认 AFC，避免额外调度和警告。
            generation_config = types.GenerateContentConfig(
                temperature=0.0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            try:
                # 尝试首选模型
                response = clients.gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=[prompt, audio_file],
                    config=generation_config,
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
                    config=generation_config,
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
