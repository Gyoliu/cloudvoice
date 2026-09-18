# Google Voice 自动化项目 (Web 端)

## 项目简介
本项目是一个基于 Web 架构的语音转换平台，包含两个核心功能：
1. **文字转语音 (TTS)**：支持普通播放和 Edge-only 流式播放；普通模式失败时可自动降级至 Google Gemini TTS。
2. **语音转文字 (STT)**：桌面 Edge 优先使用浏览器 SpeechRecognition；不可用时自动切换现有后端，上传文件仍由服务端识别。

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
│   ├── services/         # Edge TTS、Gemini STT/TTS 服务与降级逻辑
│   └── tests/            # 后端单元测试
└── README.md             # 项目入口文档
```

## 开发流程
1. 参阅 `docs/` 下的架构与 API 接口说明进行讨论和确认。
2. 确认无误后，分别在 `frontend` 和 `backend` 目录下进行代码实现。


#### 1. 启动服务（前端 + API 同源）

  打开一个终端，在项目根目录：

    cd /Users/gyo/Downloads/googlecloudvoice

    # 激活虚拟环境（本仓库 .venv 由 uv 创建，默认无 pip）
    source .venv/bin/activate

    # 如果你还没安装依赖，请先安装：
    # uv pip install --require-hashes -r ./backend/requirements.lock
    # 或按 requirements.txt 安装（含 python-socks 等新增依赖）：
    # uv pip install -r ./backend/requirements.txt

    # 启动 FastAPI（同时提供 /api 与 frontend 静态页面）
    python ./backend/main.py

  启动成功后，服务会在 http://0.0.0.0:8000 监听。
  浏览器访问 👉 http://localhost:8000/

  确保 `backend/.env` 已配置：
  - `GEMINI_API_KEY`：STT / TTS Google 降级路径
  - `API_ACCESS_TOKEN`：所有 `/api` 接口访问 Token

  TTS 首选 `zh-CN-YunxiNeural`，不需要额外的 Microsoft 凭据。
  本项目使用 Gemini Developer API，不需要 `GOOGLE_APPLICATION_CREDENTIALS`。

  前端可通过 `index.html` 的 `google-voice-api-token` meta 填写同一 Token；
  留空时首次调用接口会弹窗输入，并写入 `sessionStorage`。

#### 2. 体验

  你就可以体验普通 TTS、Edge TTS 边生成边播放、文件上传 STT 和实时麦克风转录。
  桌面版 Microsoft Edge 87 及以上且 SpeechRecognition 可用时，实时录音直接使用浏览器识别，
  不请求本项目后端；其他浏览器、旧版 Edge、策略禁用或运行时服务失败时，自动切换 WebSocket + PCM 路径。
  后端路径优先使用 AudioWorklet，缺少该能力时自动使用兼容采集方式。

  同源部署时 `frontend/index.html` 中的 `google-voice-api-origin` 可留空。
  若前后端分端口调试，可再单独起前端静态服务，并按需填写该 meta；HTTPS 页面会自动使用 `wss://` WebSocket。

#### 3. 质量检查

    cd /Users/gyo/Downloads/googlecloudvoice
    pip install --require-hashes -r backend/requirements-dev.lock
    pytest
    ruff check backend
    node --check frontend/app.js
    node --check frontend/pcm-recorder-worklet.js
