import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Annotated

from core.audio import pcm_to_wav
from core.config import clients
from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from google.genai import types
from services.stt_service import STTService

logger = logging.getLogger(__name__)
router = APIRouter()

LIVE_MODEL = "gemini-3.1-flash-live-preview"
LIVE_RECEIVE_TIMEOUT_SECONDS = 10
DEFAULT_SAMPLE_RATE = 16_000

# 支持上传给 Gemini 的音频 MIME 类型及其安全临时文件后缀。
AUDIO_MIME_SUFFIXES = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
}


class StreamProtocolError(ValueError):
    """浏览器发送了不符合实时转录协议的消息。"""


@dataclass
class StreamState:
    # 浏览器 AudioContext 的实际采样率。
    sample_rate: int = DEFAULT_SAMPLE_RATE
    # 是否已经消费浏览器发送的停止指令。
    stop_received: bool = False
    # Live API 已确认的转录片段。
    final_segments: list[str] = field(default_factory=list)


def normalize_audio_mime_type(content_type: str | None) -> str:
    """规范化上传文件的 MIME 类型并拒绝非音频内容。"""
    normalized = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
    if normalized not in AUDIO_MIME_SUFFIXES:
        raise HTTPException(status_code=415, detail="仅支持常见音频文件格式")
    return normalized


def parse_control_message(raw_message: str) -> dict:
    """解析并验证实时转录 WebSocket 控制消息。"""
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise StreamProtocolError("控制消息必须是合法 JSON") from exc
    if not isinstance(message, dict):
        raise StreamProtocolError("控制消息必须是 JSON 对象")
    return message


def update_stream_state(message: dict, state: StreamState) -> None:
    """根据浏览器控制消息更新实时音频流状态。"""
    action = message.get("action")
    if action == "start":
        sample_rate = message.get("sample_rate")
        if not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 96_000:
            raise StreamProtocolError("采样率必须是 8000 到 96000 之间的整数")
        state.sample_rate = sample_rate
        return
    if action == "stop":
        state.stop_received = True
        return
    raise StreamProtocolError("不支持的实时转录控制指令")


def raise_if_disconnected(message: dict) -> None:
    """将 Starlette 原始断开消息转换为可统一处理的异常。"""
    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(code=message.get("code", 1000))


@router.post("/api/stt/upload")
async def speech_to_text_upload_route(file: Annotated[UploadFile, File()]):
    mime_type = normalize_audio_mime_type(file.content_type)
    tmp_path = ""
    try:
        # 按真实 MIME 类型保存后缀；暂按用户要求保留一次性读取且不设置大小上限。
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=AUDIO_MIME_SUFFIXES[mime_type],
        ) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Gemini SDK 是同步接口，放入工作线程，避免阻塞 FastAPI 事件循环。
        text = await asyncio.to_thread(STTService.transcribe_file, tmp_path, mime_type)
        return {"status": "success", "data": {"text": text}}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("上传音频转录失败")
        raise HTTPException(status_code=502, detail="语音识别服务暂时不可用") from exc
    finally:
        await file.close()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass


async def run_fallback_buffer_mode(
    websocket: WebSocket,
    audio_buffer: bytearray | None = None,
    state: StreamState | None = None,
) -> None:
    """将浏览器 PCM 缓冲封装成 WAV，再使用批处理模型完成转录。"""
    audio_buffer = audio_buffer if audio_buffer is not None else bytearray()
    state = state or StreamState()
    tmp_path = ""

    try:
        while not state.stop_received:
            message = await websocket.receive()
            raise_if_disconnected(message)
            if message.get("bytes") is not None:
                audio_buffer.extend(message["bytes"])
                await websocket.send_json(
                    {
                        "is_final": False,
                        "status": f"[降级模式] 持续接收中... ({len(audio_buffer)} 字节)",
                    }
                )
                continue

            if message.get("text") is not None:
                update_stream_state(parse_control_message(message["text"]), state)

        if not audio_buffer:
            await websocket.send_json({"is_final": True, "text": "未收到音频数据"})
            return

        await websocket.send_json(
            {
                "is_final": False,
                "status": "[降级模式] 录音结束，正在使用批处理模型解析...",
            }
        )
        wav_content = pcm_to_wav(bytes(audio_buffer), sample_rate=state.sample_rate)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(wav_content)
            tmp_path = tmp.name

        text = await asyncio.to_thread(STTService.transcribe_file, tmp_path, "audio/wav")
        await websocket.send_json({"is_final": True, "text": text})
    except WebSocketDisconnect:
        logger.info("WebSocket 客户端在降级转录期间断开连接")
    except StreamProtocolError:
        logger.warning("收到不合法的实时转录控制消息", exc_info=True)
        await websocket.send_json({"is_final": True, "error": "实时转录协议错误"})
    except Exception:
        logger.exception("降级批处理转录失败")
        await websocket.send_json({"is_final": True, "error": "语音识别服务暂时不可用"})
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass


async def browser_to_gemini(
    websocket: WebSocket,
    session,
    audio_buffer: bytearray,
    state: StreamState,
) -> None:
    """将浏览器发送的 PCM 分片转发给 Gemini Live API。"""
    while True:
        message = await websocket.receive()
        raise_if_disconnected(message)
        if message.get("bytes") is not None:
            pcm_chunk = message["bytes"]
            # 同步保留缓冲，确保 Live API 中途失败时可以无损降级。
            audio_buffer.extend(pcm_chunk)
            await session.send_realtime_input(
                audio=types.Blob(
                    data=pcm_chunk,
                    mime_type=f"audio/pcm;rate={state.sample_rate}",
                )
            )
            continue

        if message.get("text") is not None:
            update_stream_state(parse_control_message(message["text"]), state)
            if state.stop_received:
                await session.send_realtime_input(audio_stream_end=True)
                return


async def gemini_to_browser(websocket: WebSocket, session, state: StreamState) -> None:
    """读取 Gemini 输入音频转录事件并推送给浏览器。"""
    async for response in session.receive():
        server_content = getattr(response, "server_content", None)
        if not server_content:
            continue

        interim = getattr(server_content, "interim_input_transcription", None)
        interim_text = (getattr(interim, "text", "") or "").strip()
        if interim_text:
            visible_text = "".join(state.final_segments) + interim_text
            await websocket.send_json({"is_final": False, "text": visible_text, "phase": "interim"})

        final = getattr(server_content, "input_transcription", None)
        final_text = (getattr(final, "text", "") or "").strip()
        if final_text and (not state.final_segments or state.final_segments[-1] != final_text):
            state.final_segments.append(final_text)
            await websocket.send_json(
                {
                    "is_final": False,
                    "text": "".join(state.final_segments),
                    "phase": "confirmed",
                }
            )

        if state.stop_received and getattr(server_content, "turn_complete", False):
            return


async def run_live_transcription(
    websocket: WebSocket,
    session,
    audio_buffer: bytearray,
    state: StreamState,
) -> None:
    """协调浏览器上行与 Gemini 下行任务，并在停止后输出最终文本。"""
    sender_task = asyncio.create_task(browser_to_gemini(websocket, session, audio_buffer, state))
    receiver_task = asyncio.create_task(gemini_to_browser(websocket, session, state))

    try:
        done, _ = await asyncio.wait(
            {sender_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if receiver_task in done and not sender_task.done():
            receiver_error = receiver_task.exception()
            if receiver_error:
                raise receiver_error
            raise RuntimeError("Gemini Live 接收通道提前结束")

        await sender_task
        try:
            await asyncio.wait_for(receiver_task, timeout=LIVE_RECEIVE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("等待 Gemini Live 最终转录超时，将使用已确认片段")

        final_text = "".join(state.final_segments).strip()
        if not final_text and audio_buffer:
            raise RuntimeError("Gemini Live 未返回有效转录文本")
        await websocket.send_json({"is_final": True, "text": final_text or "未识别到语音内容"})
    finally:
        for task in (sender_task, receiver_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)


@router.websocket("/api/stt/stream")
async def speech_to_text_stream_route(websocket: WebSocket):
    await websocket.accept()
    audio_buffer = bytearray()
    state = StreamState()

    try:
        live_api = getattr(getattr(clients.gemini_client, "aio", None), "live", None)
        if live_api is None:
            await websocket.send_json(
                {
                    "is_final": False,
                    "status": "Live API 不可用，已降级为缓冲识别模式",
                }
            )
            await run_fallback_buffer_mode(websocket, audio_buffer, state)
            return

        live_config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )
        async with live_api.connect(model=LIVE_MODEL, config=live_config) as session:
            await websocket.send_json(
                {
                    "is_final": False,
                    "status": f"已连接 Live API ({LIVE_MODEL})，请说话...",
                }
            )
            await run_live_transcription(websocket, session, audio_buffer, state)
    except WebSocketDisconnect:
        logger.info("WebSocket 客户端断开连接")
        return
    except StreamProtocolError:
        logger.warning("实时转录协议错误", exc_info=True)
        await websocket.send_json({"is_final": True, "error": "实时转录协议错误"})
    except Exception:
        logger.exception("Live API 转录失败，切换到缓冲批处理模式")
        await websocket.send_json(
            {
                "is_final": False,
                "status": "Live API 不可用，已降级为缓冲识别模式",
            }
        )
        await run_fallback_buffer_mode(websocket, audio_buffer, state)
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            # WebSocket 已由客户端或框架关闭时无需重复关闭。
            pass
