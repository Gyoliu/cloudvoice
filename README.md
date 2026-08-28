# Google Voice 自动化项目 (Web 端)

## 项目简介
本项目是一个基于 Web 架构的语音转换平台，包含两个核心功能：
1. **文字转语音 (TTS)**：输入文字，生成音频文件并在网页端播放。
2. **语音转文字 (STT)**：在网页端实时录音或上传音频文件，服务端识别后返回文字。

## 工程目录结构
```text
googlecloudvoice/
├── docs/                 # 项目文档 (架构设计、API 接口文档等)
│   ├── architecture.md   # 系统架构设计与免费额度说明
│   └── api_spec.md       # 后端 API 接口文档
├── frontend/             # 前端项目 (Web UI)
│   ├── app.js            # 页面交互、API 调用和 PCM 录音控制
│   └── pcm-recorder-worklet.js # AudioWorklet PCM 采集器
├── backend/              # 后端项目 (API 服务)
│   ├── routers/          # HTTP 与 WebSocket 路由
│   ├── services/         # Gemini STT/TTS 服务
│   └── tests/            # 后端单元测试
└── README.md             # 项目入口文档
```

## 开发流程
1. 参阅 `docs/` 下的架构与 API 接口说明进行讨论和确认。
2. 确认无误后，分别在 `frontend` 和 `backend` 目录下进行代码实现。


#### 1. 启动后端 (API 服务)

  打开一个终端终端，进入 backend 目录：

    cd /Users/gyo/Downloads/googlecloudvoice/backend

    # 如果你还没安装依赖，请先安装：
    # python -m venv .venv
    # source .venv/bin/activate
    # 生产依赖（包含传递依赖哈希）
    # pip install --require-hashes -r requirements.lock

    # 启动 FastAPI 后端服务
    python main.py

  启动成功后，后端会在 http://0.0.0.0:8000 监听请求。
  确保 `backend/.env` 文件中已经配置 `GEMINI_API_KEY`。本项目使用 Gemini Developer API，
  不需要 `GOOGLE_APPLICATION_CREDENTIALS`。

#### 2. 启动前端 (Web 界面)

  现代浏览器（尤其是 Chrome）为了安全，要求必须在 http/https 环境下才能调取麦克风，直接双击打开 .html (file://)
  可能会导致无法授权麦克风权限。

  所以，请再开一个新的终端，进入前端目录并启动一个极简的 Web 服务器：

    cd /Users/gyo/Downloads/googlecloudvoice/frontend

    # 使用 Python 自带的简易服务器启动前端
    python -m http.server 3000

  #### 3. 体验！

  现在，在浏览器里访问 👉 http://localhost:3000
  你就可以体验 TTS、文件上传 STT，以及利用 WebSocket 和 PCM 流实现的实时麦克风转录。
  现代浏览器优先使用 AudioWorklet，缺少该能力时自动使用兼容采集路径。

  如果前后端不是同源部署，可编辑 `frontend/index.html` 中的 `google-voice-api-origin` meta 配置；
  HTTPS 页面会自动使用 `wss://` WebSocket。

#### 4. 质量检查

    cd /Users/gyo/Downloads/googlecloudvoice
    pip install --require-hashes -r backend/requirements-dev.lock
    pytest
    ruff check backend
    node --check frontend/app.js
    node --check frontend/pcm-recorder-worklet.js
