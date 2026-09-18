import pytest
from core.auth import assert_websocket_api_token, tokens_match
from fastapi import WebSocketException


def test_tokens_match_rejects_empty_values():
    assert tokens_match(None, "secret") is False
    assert tokens_match("", "secret") is False
    assert tokens_match("secret", "") is False
    assert tokens_match("secret", "secret") is True


def test_websocket_requires_token_query(monkeypatch):
    monkeypatch.setenv("API_ACCESS_TOKEN", "test-api-token")

    class FakeWebSocket:
        query_params = {}

    with pytest.raises(WebSocketException) as exc_info:
        assert_websocket_api_token(FakeWebSocket())
    assert exc_info.value.code == 1008


def test_websocket_accepts_matching_token(monkeypatch):
    monkeypatch.setenv("API_ACCESS_TOKEN", "test-api-token")

    class FakeWebSocket:
        query_params = {"token": "test-api-token"}

    assert_websocket_api_token(FakeWebSocket())
