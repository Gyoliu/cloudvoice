import asyncio
import logging
from pathlib import Path

from clients.llm_aggregator import LLMAggregatorClient
from core.ai_observability import AICallObservation, binary_content, text_content
from core.config import AI_PROVIDER_MODE, AIProviderMode, clients
from core.error_logging import safe_exception_chain
from google.genai import types

logger = logging.getLogger(__name__)


class STTServiceError(RuntimeError):
    """STT 服务调用或响应解析失败。"""


class STTService:
    @staticmethod
    async def transcribe_file(file_path: str, mime_type: str | None = None) -> str:
        """按启动配置选择聚合平台或原有 Gemini SDK 转录链路。"""
        if AI_PROVIDER_MODE is AIProviderMode.AGGREGATOR:
            aggregator = LLMAggregatorClient()
            try:
                transcript = await aggregator.transcribe_audio(file_path, mime_type)
                logger.info("LLM 聚合平台 STT 转录成功")
                return transcript
            except Exception as aggregator_error:
                logger.error(
                    "聚合平台模式 STT 调用失败：%s",
                    safe_exception_chain(aggregator_error),
                    exc_info=True,
                )
                raise STTServiceError("聚合平台 STT 服务暂时不可用") from aggregator_error

        return await asyncio.to_thread(
            STTService._transcribe_with_gemini,
            file_path,
            mime_type,
        )

    @staticmethod
    def _transcribe_with_gemini(file_path: str, mime_type: str | None = None) -> str:
        """
        使用现有 Gemini SDK 解析本地音频文件为文本。

        优先使用 gemini-3.6-flash，如果失败降级至 gemini-3.5-flash
        """
        if not clients.gemini_client:
            raise STTServiceError("Gemini 客户端未初始化")

        audio_file = None
        try:
            try:
                file_size = Path(file_path).stat().st_size
            except OSError:
                # 服务层测试桩或特殊文件源可能没有可查询的本地大小；
                # 可观测日志缺失字节数不应阻断实际的上传与转录流程。
                file_size = None
            audio_metadata = binary_content(
                mime_type or "application/octet-stream",
                byte_count=file_size,
            )
            # 上传到云端
            upload_config = types.UploadFileConfig(mime_type=mime_type) if mime_type else None
            upload_observation = AICallObservation.start(
                operation="stt.file_upload",
                provider="google-gemini",
                model="files.upload",
                input_data={"audio": audio_metadata},
            )
            try:
                audio_file = clients.gemini_client.files.upload(
                    file=file_path,
                    config=upload_config,
                )
            except Exception as exc:
                upload_observation.failure(exc)
                raise
            upload_observation.success(
                {
                    "file": {
                        "type": "remote_file",
                        "content": "<file omitted>",
                    }
                }
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

            model_observation = AICallObservation.start(
                operation="stt",
                provider="google-gemini",
                model="gemini-3.6-flash",
                input_data={"prompt": text_content(prompt), "audio": audio_metadata},
            )
            try:
                # 尝试首选模型
                response = clients.gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=[prompt, audio_file],
                    config=generation_config,
                )
            except Exception as primary_error:
                model_observation.failure(primary_error)
                logger.warning(
                    "STT 首选模型 gemini-3.6-flash 调用失败，切换备用模型",
                    exc_info=True,
                )
                # 降级备用模型
                model_observation = AICallObservation.start(
                    operation="stt",
                    provider="google-gemini",
                    model="gemini-3.5-flash",
                    input_data={"prompt": text_content(prompt), "audio": audio_metadata},
                )
                try:
                    response = clients.gemini_client.models.generate_content(
                        model="gemini-3.5-flash",
                        contents=[prompt, audio_file],
                        config=generation_config,
                    )
                except Exception as fallback_error:
                    model_observation.failure(fallback_error)
                    raise
            transcript = (response.text or "").strip()
            if not transcript:
                error = STTServiceError("Gemini STT 模型未返回转录文本")
                model_observation.failure(error)
                raise error
            model_observation.success({"text": text_content(transcript)})
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
