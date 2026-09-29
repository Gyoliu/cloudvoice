import logging
from contextlib import asynccontextmanager
from pathlib import Path

from core.ai_observability import (
    RequestObservation,
    bind_request_id,
    reset_request_id,
)
from core.auth import ApiTokenMiddleware
from core.config import AI_PROVIDER_MODE, AIProviderMode, get_llm_aggregator_settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routers import stt, tts

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时锁定并校验语音提供方配置，禁止运行中自动跨模式切换。"""
    if (
        AI_PROVIDER_MODE is AIProviderMode.AGGREGATOR
        and not get_llm_aggregator_settings().available
    ):
        raise RuntimeError(
            "AI_PROVIDER_MODE=aggregator 时必须配置 "
            "LLM_AGGREGATOR_BASE_URL 和 LLM_AGGREGATOR_API_KEY"
        )
    logger.info("AI 提供方模式已锁定: %s", AI_PROVIDER_MODE.value)
    yield


# 初始化 FastAPI
app = FastAPI(title="Google Voice API - MVC Architecture", lifespan=lifespan)


async def observe_http_request(request, call_next):
    """记录方法、路径、状态和耗时；不读取请求体、文件流或查询参数。"""
    observation = RequestObservation.start(
        method=request.method,
        path=request.url.path,
        transport="http",
    )
    context_token = bind_request_id(observation.request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = observation.request_id
        observation.success(response.status_code)
        return response
    except Exception as exc:
        observation.failure(exc)
        raise
    finally:
        reset_request_id(context_token)

# Token 先注册、CORS 后注册：CORS 位于 Token 外层，预检 OPTIONS 不会被 Token 拦截。
app.add_middleware(ApiTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 最后注册的请求日志位于最外层，因此鉴权失败和 CORS 预检也能被观测。
app.middleware("http")(observe_http_request)

# 挂载 API 路由（须在静态目录之前注册，避免被 / 挂载抢占）
app.include_router(tts.router, tags=["TTS"])
app.include_router(stt.router, tags=["STT"])

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        # 开发重载时不要无限等待仍保持连接的浏览器 WebSocket。
        timeout_graceful_shutdown=5,
    )
