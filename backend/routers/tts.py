import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from schemas.requests import TTSRequest
from services.tts_service import TTSService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/tts")
async def text_to_speech_route(req: TTSRequest):
    try:
        # Gemini SDK 是同步接口，放入工作线程，避免阻塞 FastAPI 事件循环。
        audio_content = await asyncio.to_thread(
            TTSService.generate_audio,
            req.text,
            req.voice_name,
        )
        return Response(content=audio_content, media_type="audio/wav")
    except Exception as exc:
        logger.exception("TTS 请求处理失败")
        raise HTTPException(status_code=502, detail="语音生成服务暂时不可用") from exc
