import pytest

TEST_API_ACCESS_TOKEN = "test-api-token"


@pytest.fixture(autouse=True)
def configure_api_access_token(monkeypatch):
    """测试默认使用原有提供方模式，隔离真实聚合平台网络。"""
    monkeypatch.setenv("API_ACCESS_TOKEN", TEST_API_ACCESS_TOKEN)
    monkeypatch.setenv("AI_PROVIDER_MODE", "original")
