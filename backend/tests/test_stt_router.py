import asyncio

import pytest
from fastapi import HTTPException
from routers.stt import (
    StreamProtocolError,
    StreamState,
    browser_to_gemini,
    normalize_audio_mime_type,
    parse_control_message,
    update_stream_state,
)


def test_normalize_audio_mime_type_removes_parameters():
    """上传 MIME 类型携带参数时仍能正确识别。"""
    assert normalize_audio_mime_type("audio/webm; codecs=opus") == "audio/webm"


def test_normalize_audio_mime_type_rejects_non_audio():
    """非音频上传应返回 415。"""
    with pytest.raises(HTTPException) as exc_info:
        normalize_audio_mime_type("text/plain")
    assert exc_info.value.status_code == 415


def test_stream_control_updates_sample_rate_and_stop_state():
    """start 和 stop 控制消息应正确更新流状态。"""
    state = StreamState()
    update_stream_state(parse_control_message('{"action":"start","sample_rate":48000}'), state)
    assert state.sample_rate == 48_000

    update_stream_state(parse_control_message('{"action":"stop"}'), state)
    assert state.stop_received is True


def test_stream_control_rejects_unknown_action():
    """未知控制指令不应进入 Live 或降级处理。"""
    with pytest.raises(StreamProtocolError):
        update_stream_state({"action": "unknown"}, StreamState())


def test_browser_to_gemini_sends_pcm_before_stream_end():
    """实时流应按采样率发送 PCM，并在全部音频之后发送结束信号。"""

    class FakeWebSocket:
        def __init__(self):
            self.messages = iter(
                [
                    {
                        "type": "websocket.receive",
                        "text": '{"action":"start","sample_rate":48000}',
                    },
                    {"type": "websocket.receive", "bytes": b"\x01\x02"},
                    {"type": "websocket.receive", "text": '{"action":"stop"}'},
                ]
            )

        async def receive(self):
            return next(self.messages)

    class FakeSession:
        def __init__(self):
            self.calls = []

        async def send_realtime_input(self, **kwargs):
            self.calls.append(kwargs)

    websocket = FakeWebSocket()
    session = FakeSession()
    audio_buffer = bytearray()
    state = StreamState()

    asyncio.run(browser_to_gemini(websocket, session, audio_buffer, state))

    assert audio_buffer == b"\x01\x02"
    assert session.calls[0]["audio"].mime_type == "audio/pcm;rate=48000"
    assert session.calls[-1] == {"audio_stream_end": True}
