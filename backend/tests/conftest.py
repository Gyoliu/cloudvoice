import pytest

TEST_API_ACCESS_TOKEN = "test-api-token"


@pytest.fixture(autouse=True)
def configure_api_access_token(monkeypatch):
    """测试默认启用 API Token，避免未配置时全部 /api 返回 503。"""
    monkeypatch.setenv("API_ACCESS_TOKEN", TEST_API_ACCESS_TOKEN)
