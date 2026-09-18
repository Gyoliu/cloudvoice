import asyncio

import pytest
from fastapi import HTTPException
from routers import stt as stt_router
from routers.stt import (
    LIVE_LANGUAGE_CODE,
    LIVE_MODEL,
    StreamProtocolError,
    StreamState,
    browser_to_gemini,
    build_live_transcription_config,
    classify_live_failure,
    connect_live_with_retry,
    gemini_to_browser,
    normalize_audio_mime_type,
    parse_control_message,
    speech_to_text_stream_route,
    update_stream_state,
)


def test_live_failure_classifies_nested_connection_reset():
    """TLS/WebSocket 握手重置应视为可降级连接故障。"""
    try:
        try:
            raise ConnectionResetError("connection reset during TLS handshake")
        except ConnectionResetError as exc:
            raise RuntimeError("Live API connect failed") from exc
    except RuntimeError as exc:
        assert classify_live_failure(exc) == "connection"


def test_live_failure_classifies_websocket_authentication_error():
    """WebSocket 1008 包装的凭证错误应阻止无意义的批处理重试。"""
    error = RuntimeError(
        "1008 Request had invalid authentication credentials: "
        "ACCESS_TOKEN_TYPE_UNSUPPORTED"
    )

    assert classify_live_failure(error) == "authentication"


def test_live_failure_preserves_unexpected_errors():
    """未知代码异常应保留完整堆栈，便于排查真实缺陷。"""
    assert classify_live_failure(ValueError("invalid server payload")) == "unexpected"


def test_stream_route_stops_immediately_on_authentication_failure(monkeypatch):
    """Live 认证失败时不应继续调用共用同一无效凭证的批处理服务。"""

    class FailingConnection:
        async def __aenter__(self):
            raise RuntimeError(
                "1008 Request had invalid authentication credentials: "
                "ACCESS_TOKEN_TYPE_UNSUPPORTED"
            )

        async def __aexit__(self, *_args):
            return False

    class FakeLiveApi:
        def __init__(self):
            self.connect_calls = 0

        def connect(self, **_kwargs):
            self.connect_calls += 1
            return FailingConnection()

    class FakeWebSocket:
        def __init__(self):
            self.messages = []
            self.closed = False
            self.query_params = {"token": "test-api-token"}

        async def accept(self):
            return None

        async def send_json(self, message):
            self.messages.append(message)

        async def receive(self):
            raise AssertionError("认证失败后不应进入缓冲批处理接收循环")

        async def close(self, *args, **kwargs):
            self.closed = True

    fake_live_api = FakeLiveApi()
    fake_client = type(
        "FakeClient",
        (),
        {"aio": type("FakeAsyncClient", (), {"live": fake_live_api})()},
    )()
    monkeypatch.setattr(stt_router.clients, "gemini_client", fake_client)
    websocket = FakeWebSocket()

    asyncio.run(speech_to_text_stream_route(websocket))

    assert websocket.messages == [
        {
            "is_final": False,
            "status": "Live API 首次连接失败，正在快速重试...",
        },
        {
            "is_final": True,
            "error": "Gemini 凭证无效，请更新 GEMINI_API_KEY 后重试",
        }
    ]
    assert fake_live_api.connect_calls == 2
    assert websocket.closed is True


def test_live_connect_retries_handshake_once_before_streaming(monkeypatch):
    """瞬时 WebSocket 握手重置应快速重连，并继续使用实时会话。"""

    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    class FakeConnection:
        def __init__(self, should_fail):
            self.should_fail = should_fail

        async def __aenter__(self):
            if self.should_fail:
                raise ConnectionResetError("temporary TLS reset")
            return "live-session"

        async def __aexit__(self, *_args):
            return False

    class FakeLiveApi:
        def __init__(self):
            self.connect_calls = 0

        def connect(self, **_kwargs):
            self.connect_calls += 1
            return FakeConnection(should_fail=self.connect_calls == 1)

    async def run_test():
        websocket = FakeWebSocket()
        live_api = FakeLiveApi()
        monkeypatch.setattr(stt_router, "LIVE_CONNECT_RETRY_DELAY_SECONDS", 0)

        async with connect_live_with_retry(websocket, live_api, object()) as session:
            assert session == "live-session"

        assert live_api.connect_calls == 2
        assert websocket.messages == [
            {
                "is_final": False,
                "status": "Live API 首次连接失败，正在快速重试...",
            }
        ]

    asyncio.run(run_test())


def test_live_transcription_uses_dedicated_chinese_model():
    """实时 STT 应使用专用模型并固定简体普通话，避免短句误判语种。"""
    config = build_live_transcription_config()

    assert LIVE_MODEL == "gemini-3.5-transcribe-live"
    assert config.response_modalities == ["TEXT"]
    assert config.input_audio_transcription.language_codes == [LIVE_LANGUAGE_CODE]
    assert config.input_audio_transcription.mode == "SMART"


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


def test_gemini_receiver_continues_after_normal_turn_completion():
    """receive() 的单轮正常结束不应被误判为 Live 通道断开。"""

    class FakeWebSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    state = StreamState()

    class FakeSession:
        def __init__(self):
            self.receive_calls = 0

        def receive(self):
            self.receive_calls += 1
            call_number = self.receive_calls

            async def stream():
                if call_number == 1:
                    yield type(
                        "Response",
                        (),
                        {"server_content": type("ServerContent", (), {"turn_complete": True})()},
                    )()
                    return

                state.stop_received = True
                transcription = type("Transcription", (), {"text": "测试结果"})()
                yield type(
                    "Response",
                    (),
                    {
                        "server_content": type(
                            "ServerContent",
                            (),
                            {
                                "turn_complete": True,
                                "input_transcription": transcription,
                            },
                        )()
                    },
                )()

            return stream()

    websocket = FakeWebSocket()
    session = FakeSession()

    asyncio.run(gemini_to_browser(websocket, session, state))

    assert session.receive_calls == 2
    assert state.final_segments == ["测试结果"]
    assert websocket.messages[-1]["phase"] == "confirmed"
