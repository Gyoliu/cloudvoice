import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from schemas.requests import EdgeTTSStreamRequest, TTSRequest
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
async def stream_text_to_speech_route(req: EdgeTTSStreamRequest):
    """仅使用 Edge TTS 返回可供浏览器边接收边播放的 MP3 分片。"""
    audio_stream = TTSService.stream_edge_audio(req.text)
    try:
        # 返回响应前先取得首个音频分片，确保建连失败仍能返回明确的 502。
        first_chunk = await anext(audio_stream)
    except Exception as exc:
        await audio_stream.aclose()
        logger.exception("Edge TTS 流式请求启动失败")
        raise HTTPException(status_code=502, detail="Edge 流式语音服务暂时不可用") from exc

    async def response_body():
        try:
            yield first_chunk
            async for audio_chunk in audio_stream:
                yield audio_chunk
        finally:
            # 客户端中止播放时同步关闭上游生成器和网络连接。
            await audio_stream.aclose()

    return StreamingResponse(
        response_body(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-TTS-Provider": "microsoft-edge",
        },
    )
