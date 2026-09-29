import json
import logging

from core.ai_observability import (
    AICallObservation,
    binary_content,
    bind_request_id,
    reset_request_id,
    text_content,
)


def _ai_payloads(caplog):
    """解析结构化 AI_CALL 日志，便于断言字段和关联标识。"""
    return [
        json.loads(record.getMessage().removeprefix("AI_CALL "))
        for record in caplog.records
        if record.name == "ai.observability"
    ]


def test_ai_call_logs_text_input_output_with_same_request_id(caplog):
    """文本输入输出应可观测，并通过同一 request_id/call_id 关联。"""
    caplog.set_level(logging.INFO, logger="ai.observability")
    context_token = bind_request_id("request-test-001")
    try:
        observation = AICallObservation.start(
            operation="tts",
            provider="test-provider",
            model="test-model",
            input_data={"text": text_content("你好，世界")},
        )
        observation.success({"text": text_content("调用成功")})
    finally:
        reset_request_id(context_token)

    payloads = _ai_payloads(caplog)
    assert [payload["event"] for payload in payloads] == ["request", "response"]
    assert {payload["request_id"] for payload in payloads} == {"request-test-001"}
    assert len({payload["call_id"] for payload in payloads}) == 1
    assert payloads[0]["input"]["text"]["content"] == "你好，世界"
    assert payloads[1]["output"]["text"]["content"] == "调用成功"
    assert payloads[1]["status"] == "success"


def test_ai_call_omits_binary_content_and_records_only_metadata(caplog):
    """音频和文件流不得写入日志，只记录类型、格式和字节数。"""
    caplog.set_level(logging.INFO, logger="ai.observability")
    observation = AICallObservation.start(
        operation="stt",
        provider="test-provider",
        model="test-model",
        input_data={
            "audio": binary_content("audio/wav", byte_count=4096, stream=True)
        },
    )
    observation.success(
        {"audio": binary_content("audio/mpeg", byte_count=2048, stream=True)}
    )

    payloads = _ai_payloads(caplog)
    input_audio = payloads[0]["input"]["audio"]
    output_audio = payloads[1]["output"]["audio"]
    assert input_audio == {
        "type": "binary_stream",
        "content": "<binary omitted>",
        "content_type": "audio/wav",
        "bytes": 4096,
    }
    assert output_audio["content"] == "<binary omitted>"
    assert output_audio["bytes"] == 2048


def test_text_logging_can_be_disabled_without_losing_size(monkeypatch):
    """生产环境可关闭正文日志，同时保留排障需要的字符数。"""
    monkeypatch.setenv("AI_LOG_TEXT_CONTENT", "false")

    assert text_content("敏感文本") == {
        "type": "text",
        "characters": 4,
        "content": "<text logging disabled>",
    }


def test_text_logging_truncates_oversized_fields(monkeypatch):
    """超长文本按配置截断，避免单条日志无限膨胀。"""
    monkeypatch.setenv("AI_LOG_TEXT_CONTENT", "true")
    monkeypatch.setenv("AI_LOG_TEXT_MAX_LENGTH", "3")

    assert text_content("123456") == {
        "type": "text",
        "characters": 6,
        "content": "123",
        "truncated": True,
        "logged_characters": 3,
    }
