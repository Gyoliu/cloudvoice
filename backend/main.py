from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import stt, tts

# 初始化 FastAPI
app = FastAPI(title="Google Voice API - MVC Architecture")

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由
app.include_router(tts.router, tags=["TTS"])
app.include_router(stt.router, tags=["STT"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
