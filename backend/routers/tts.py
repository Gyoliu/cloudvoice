import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from schemas.requests import TTSRequest, TTSStreamRequest
from services.tts_service import TTSService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/tts")
async def text_to_speech_route(req: TTSRequest):
    try:
        generated_audio = await TTSService.generate_audio(req.text, req.voice_name)
        return Response(
            content=generated_audio.content,
            media_type=generated_audio.media_type,
            # 标识本次实际使用的提供方，便于排查是否发生了自动降级。
            headers={"X-TTS-Provider": generated_audio.provider},
        )
    except Exception as exc:
        logger.exception("TTS 请求处理失败")
        raise HTTPException(status_code=502, detail="语音生成服务暂时不可用") from exc


@router.post("/api/tts/stream")
async def stream_text_to_speech_route(req: TTSStreamRequest):
    """透传启动时选定的聚合平台或原有 Edge TTS 音频流。"""
    try:
        # 返回响应前先取得首个音频分片，确保建连失败仍能返回明确的 502。
        started_stream = await TTSService.start_audio_stream(req.text)
    except Exception as exc:
        logger.exception("TTS 流式请求启动失败")
        raise HTTPException(status_code=502, detail="流式语音服务暂时不可用") from exc

    async def response_body():
        try:
            yield started_stream.first_chunk
            async for audio_chunk in started_stream.stream:
                yield audio_chunk
        finally:
            # 客户端中止播放时同步关闭当前所选提供方的上游连接。
            await started_stream.stream.aclose()

    return StreamingResponse(
        response_body(),
        media_type=started_stream.media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-TTS-Provider": started_stream.provider,
        },
    )
