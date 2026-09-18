from pathlib import Path

from core.auth import ApiTokenMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routers import stt, tts

# 初始化 FastAPI
app = FastAPI(title="Google Voice API - MVC Architecture")

# Token 先注册、CORS 后注册：后者在最外层，预检 OPTIONS 不会被 Token 拦截。
app.add_middleware(ApiTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
