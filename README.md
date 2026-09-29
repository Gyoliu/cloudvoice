# Google Voice 自动化项目 (Web 端)

## 项目简介
本项目是一个基于 Web 架构的语音转换平台，包含两个核心功能：
1. **文字转语音 (TTS)**：启动时选择 `original` 或 `aggregator` 模式。默认 `original` 保留 Edge TTS → Google Gemini SDK 稳定链路；`aggregator` 只使用 LLM 聚合平台。
2. **语音转文字 (STT)**：桌面 Edge 优先使用浏览器 SpeechRecognition；后端批量识别严格使用启动时选定的提供方模式。

## 工程目录结构
```text
googlecloudvoice/
├── docs/                 # 项目文档 (架构设计、API 接口文档等)
│   ├── architecture.md   # 系统架构设计与免费额度说明
│   ├── api_spec.md       # 后端 API 接口文档
│   └── llm_aggregator_implementation.md # 聚合平台语音实施与流式核验
├── frontend/             # 前端项目 (Web UI)
│   ├── app.js            # 页面交互、API 调用和 PCM 录音控制
│   └── pcm-recorder-worklet.js # AudioWorklet PCM 采集器
├── backend/              # 后端项目 (API 服务)
│   ├── routers/          # HTTP 与 WebSocket 路由
│   ├── clients/          # LLM 聚合平台兼容客户端
│   ├── services/         # 启动提供方选择及各模式内部 STT/TTS 编排
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

    .venv/bin/uvicorn main:app \
  --app-dir backend \
  --host 0.0.0.0 \
  --port 8000 \
  --log-config backend/logging.json \
  --no-access-log

  # 服务器通过 systemd 部署时，日志由 journald 收集：
  # 实时查看所有服务日志
  sudo journalctl -u googlecloudvoice -f

  # 只看请求和 AI 调用
  sudo journalctl -u googlecloudvoice -f --no-pager \
    | grep --line-buffered -E 'API_REQUEST|AI_CALL'

  # 最近 100 条
  sudo journalctl -u googlecloudvoice -n 100 --no-pager

  启动成功后，服务会在 http://0.0.0.0:8000 监听。
  浏览器访问 👉 http://localhost:8000/

  确保 `backend/.env` 已配置：
  - `AI_PROVIDER_MODE=original`：默认原有稳定链路；可改为 `aggregator`
  - `GEMINI_API_KEY`：original 模式及后端 Gemini Live 实时转录
  - `LLM_AGGREGATOR_API_KEY`：仅 aggregator 模式使用的统一 API Key
  - `LLM_AGGREGATOR_BASE_URL=https://gyo.ccwu.cc/v1`
  - `API_ACCESS_TOKEN`：所有 `/api` 接口访问 Token
  - `AI_LOG_TEXT_CONTENT=true`：记录 AI 文本输入输出；设为 `false` 时只记录字符数
  - `AI_LOG_TEXT_MAX_LENGTH=12000`：单个文本日志的最大字符数，`0` 表示不截断

  `AI_PROVIDER_MODE` 在进程启动时锁定，修改后必须重启服务。两种模式不会因单次请求失败而互相切换。
  聚合平台 TTS 与 Edge 路径均使用 `zh-CN-YunxiNeural`。
  本项目使用 Gemini Developer API，不需要 `GOOGLE_APPLICATION_CREDENTIALS`。

  HTTP 与 WebSocket 请求会输出带 `request_id` 的 `API_REQUEST` 日志；各提供方调用会输出
  同一 `request_id` 下的 `AI_CALL` 输入、增量文本、结果、模型和耗时。音频、上传文件、
  PCM 分片及响应文件流不会写入日志，只记录 MIME 类型、字节数和是否为流。

  前端可通过 `index.html` 的 `google-voice-api-token` meta 填写同一 Token；
  留空时首次调用接口会弹窗输入，并写入 `sessionStorage`。

#### 2. 体验

  你可以体验普通 TTS、所选提供方的流式播放、文件上传 STT 和实时麦克风转录。
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
