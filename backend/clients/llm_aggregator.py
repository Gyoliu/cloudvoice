import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from core.ai_observability import AICallObservation, binary_content, text_content
from core.config import LLMAggregatorSettings, get_llm_aggregator_settings
from core.error_logging import safe_exception_detail

logger = logging.getLogger(__name__)


class LLMAggregatorError(RuntimeError):
    """聚合平台调用或响应解析失败。"""


class LLMAggregatorUnavailable(LLMAggregatorError):
    """聚合平台未启用或缺少必要配置。"""


class LLMAggregatorClient:
    """调用 OpenAI 兼容的聚合平台音频端点。"""

    def __init__(
        self,
        settings: LLMAggregatorSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings or get_llm_aggregator_settings()
        self.transport = transport

    def _ensure_available(self) -> None:
        if not self.settings.available:
            raise LLMAggregatorUnavailable("LLM 聚合平台未启用或配置不完整")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    def _timeout(self, operation_seconds: float) -> httpx.Timeout:
        # 流式 TTS 的 read 超时按相邻分片计算，避免长文本尚在正常生成时被总时长截断。
        return httpx.Timeout(
            connect=self.settings.connect_timeout_seconds,
            read=operation_seconds,
            write=operation_seconds,
            pool=self.settings.connect_timeout_seconds,
        )

    @staticmethod
    def _response_error_detail(
        operation: str,
        response: httpx.Response,
        requested_model: str,
    ) -> str:
        """提取允许写入日志的上游错误字段，不输出请求正文或认证头。"""
        fields: list[str] = [
            f"聚合平台 {operation} 返回 HTTP {response.status_code}",
            f"requested_model={requested_model}",
        ]
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            payload = None

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                for key in ("type", "code", "status", "message"):
                    value = error_payload.get(key)
                    if isinstance(value, (str, int, float, bool)) and str(value).strip():
                        fields.append(f"{key}={value}")
            elif isinstance(error_payload, str) and error_payload.strip():
                fields.append(f"error={error_payload}")

            for key in ("code", "type", "message", "detail"):
                value = payload.get(key)
                if isinstance(value, (str, int, float, bool)) and str(value).strip():
                    fields.append(f"{key}={value}")

        for header_name in (
            "x-request-id",
            "request-id",
            "cf-ray",
            "x-provider",
            "x-model",
            "x-routed-via",
            "x-fallback-trail",
            "content-type",
            "retry-after",
        ):
            value = response.headers.get(header_name)
            if value:
                fields.append(f"{header_name}={value}")

        # 统一执行凭证脱敏和长度限制；即使上游错误消息意外回显密钥也不会落盘。
        return safe_exception_detail(LLMAggregatorError("; ".join(fields))).removeprefix(
            "LLMAggregatorError: "
        )

    async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
        """逐块返回聚合平台 TTS 音频，不信任上游可能错误的 Content-Type。"""
        self._ensure_available()
        url = f"{self.settings.base_url}/audio/speech"
        candidate_models = [self.settings.tts_model]
        if self.settings.tts_model != "auto":
            # 聚合平台可能尚未把新模型 ID 暴露给音频端点；仍优先留在平台内用 auto 路由。
            candidate_models.append("auto")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(self.settings.tts_timeout_seconds),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                for index, model in enumerate(candidate_models):
                    observation = AICallObservation.start(
                        operation="tts",
                        provider="llm-aggregator",
                        model=model,
                        input_data={
                            "text": text_content(text),
                            "voice": self.settings.tts_voice,
                            "response_format": "mp3",
                        },
                    )
                    payload = {
                        "model": model,
                        "input": text,
                        # FreeLLMAPI 的公开契约使用 input；当前 Cloudflare 音频适配器
                        # 实际读取 text。双字段保持标准兼容并规避其字段转换缺陷。
                        "text": text,
                        "voice": self.settings.tts_voice,
                        "response_format": "mp3",
                    }
                    audio_received = False
                    audio_bytes = 0
                    try:
                        async with client.stream(
                            "POST",
                            url,
                            headers={**self._headers(), "Content-Type": "application/json"},
                            json=payload,
                        ) as response:
                            if response.status_code >= 400:
                                await response.aread()
                                error_detail = self._response_error_detail(
                                    "TTS",
                                    response,
                                    model,
                                )
                                error = LLMAggregatorError(error_detail)
                                observation.failure(error)
                                has_auto_retry = (
                                    response.status_code == 400
                                    and index + 1 < len(candidate_models)
                                )
                                if has_auto_retry:
                                    logger.warning(
                                        "%s；已配置模型=%s，改用 auto 路由",
                                        error_detail,
                                        model,
                                    )
                                    continue
                                raise error
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    audio_received = True
                                    audio_bytes += len(chunk)
                                    yield chunk
                    except (GeneratorExit, asyncio.CancelledError):
                        observation.cancelled(
                            binary_content(
                                "application/octet-stream",
                                byte_count=audio_bytes,
                                stream=True,
                            )
                        )
                        raise
                    except Exception as exc:
                        observation.failure(exc)
                        raise

                    if not audio_received:
                        error = LLMAggregatorError("聚合平台 TTS 未返回音频数据")
                        observation.failure(error)
                        raise error
                    observation.success(
                        binary_content(
                            response.headers.get("content-type", "application/octet-stream"),
                            byte_count=audio_bytes,
                            stream=True,
                        )
                    )
                    return
        except LLMAggregatorError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise LLMAggregatorError(
                f"聚合平台 TTS 网络调用失败；{safe_exception_detail(exc)}"
            ) from exc

    async def synthesize_speech(self, text: str) -> bytes:
        """收集聚合平台音频分片，供普通非流式 TTS 接口返回。"""
        chunks = [chunk async for chunk in self.stream_speech(text)]
        return b"".join(chunks)

    async def transcribe_audio(self, file_path: str, mime_type: str | None = None) -> str:
        """通过 OpenAI 兼容的 multipart 接口转录本地音频文件。"""
        self._ensure_available()
        path = Path(file_path)
        url = f"{self.settings.base_url}/audio/transcriptions"
        candidate_models = [self.settings.stt_model]
        if self.settings.stt_model != "auto":
            candidate_models.append("auto")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(self.settings.stt_timeout_seconds),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                for index, model in enumerate(candidate_models):
                    file_size = path.stat().st_size
                    observation = AICallObservation.start(
                        operation="stt",
                        provider="llm-aggregator",
                        model=model,
                        input_data={
                            "audio": binary_content(
                                mime_type or "application/octet-stream",
                                byte_count=file_size,
                            )
                        },
                    )
                    with path.open("rb") as audio_file:
                        try:
                            response = await client.post(
                                url,
                                headers=self._headers(),
                                data={"model": model},
                                files={
                                    "file": (
                                        path.name,
                                        audio_file,
                                        mime_type or "application/octet-stream",
                                    )
                                },
                            )
                        except Exception as exc:
                            observation.failure(exc)
                            raise
                    if response.status_code >= 400:
                        error_detail = self._response_error_detail("STT", response, model)
                        error = LLMAggregatorError(error_detail)
                        observation.failure(error)
                        has_auto_retry = (
                            response.status_code == 400
                            and index + 1 < len(candidate_models)
                        )
                        if has_auto_retry:
                            logger.warning(
                                "%s；已配置模型=%s，改用 auto 路由",
                                error_detail,
                                model,
                            )
                            continue
                        raise error
                    try:
                        payload = response.json()
                    except json.JSONDecodeError as exc:
                        error = LLMAggregatorError("聚合平台 STT 返回了无效 JSON")
                        observation.failure(error)
                        raise error from exc
                    transcript = payload.get("text") if isinstance(payload, dict) else None
                    if not isinstance(transcript, str) or not transcript.strip():
                        error = LLMAggregatorError("聚合平台 STT 未返回转录文本")
                        observation.failure(error)
                        raise error
                    normalized_transcript = transcript.strip()
                    observation.success({"text": text_content(normalized_transcript)})
                    return normalized_transcript
        except LLMAggregatorError:
            raise
        except (OSError, httpx.HTTPError, TimeoutError) as exc:
            raise LLMAggregatorError(
                f"聚合平台 STT 调用失败；{safe_exception_detail(exc)}"
            ) from exc

        raise LLMAggregatorError("聚合平台 STT 路由异常结束")
