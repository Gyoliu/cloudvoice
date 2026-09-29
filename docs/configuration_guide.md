# 核心模型增强与个性化参数配置指南

项目通过 `AI_PROVIDER_MODE` 在启动时固定选择原有实现或 LLM 聚合平台。默认 `original`，优先保证原有 Edge/Gemini 链路稳定性；`aggregator` 模式只使用聚合平台，不做跨模式自动降级。Gemini Live 实时转录不经过聚合平台。

---

## 1. 文字转语音 (TTS) 个性化参数
**`original` 模式（默认）**：普通 TTS 使用 Microsoft Edge TTS（MP3），Edge 失败后使用 Google Gemini SDK（WAV）。Google 路径优先 `gemini-3.8-flash-tts`，再保留旧模型兼容链；流式 TTS 只使用 Edge。

**`aggregator` 模式**：普通和流式 TTS 只调用聚合平台 `/v1/audio/speech`，模型路由为 `auto`，音色为 `zh-CN-YunxiNeural`。平台失败时直接返回错误，不切换 Edge 或 Google。

**流式播放**：`POST /api/tts/stream` 按启动模式透传 Edge MP3 或聚合平台 HTTP 音频响应。浏览器可流式播放聚合平台当前实际返回的 PCM WAV，也兼容 Edge MP3。聚合平台当前仍是完整合成后响应，不是生成阶段的 SSE 音频增量；详见 `docs/llm_aggregator_implementation.md`。

### 可配置参数：
*   **`voice_name`（original 模式 Gemini 音色）**
    *   **作用**：仅在 `original` 模式中 Edge 失败并进入 Gemini SDK 后决定基础音色，默认 `Charon` 男声。
    *   **可选值**：`Aoede` (温和女声)、`Puck` (活力男声)、`Charon` (沉稳男声)、`Kore` (清脆女声)、`Fenrir` (粗犷男声)、`Leda` (知性女声)。
    *   *配置方法*：通过 `/api/tts` 请求字段传入；聚合平台与 Edge 路径均使用 `zh-CN-YunxiNeural`。

### 提供方与聚合平台环境变量

- `AI_PROVIDER_MODE=original`：默认原有稳定模式；可选 `aggregator`。修改后必须重启服务。
- `LLM_AGGREGATOR_BASE_URL=https://gyo.ccwu.cc/v1`
- `LLM_AGGREGATOR_API_KEY=$YOUR_KEY`
- `LLM_AGGREGATOR_TTS_MODEL=auto`：实测音频端点不接受 `auto:fast`；程序会自动将其规范为 `auto`。
- `LLM_AGGREGATOR_TTS_VOICE=zh-CN-YunxiNeural`
- `LLM_AGGREGATOR_STT_MODEL=auto:fast`
- `LLM_AGGREGATOR_CONNECT_TIMEOUT_SECONDS=3`
- `LLM_AGGREGATOR_TTS_TIMEOUT_SECONDS=20`
- `LLM_AGGREGATOR_STT_TIMEOUT_SECONDS=45`
- `AI_LOG_TEXT_CONTENT=true`：默认记录 AI 文本输入输出；设为 `false` 时正文替换为禁用标记，仅保留字符数。
- `AI_LOG_TEXT_MAX_LENGTH=12000`：单个文本字段最多记录的字符数；`0` 表示不截断。

`LLM_AGGREGATOR_ENABLED` 已不再参与路由选择。即使服务器保留聚合平台 Key，`AI_PROVIDER_MODE=original` 也不会自动调用聚合平台。

### 请求与 AI 调用日志

- `API_REQUEST`：记录 HTTP/WebSocket 的 `request_id`、方法、路径、传输类型、状态和耗时。不会读取或记录查询参数、请求头、请求体和上传内容。
- `AI_CALL`：记录 `request_id`、`call_id`、操作、提供方、模型、耗时以及 AI 文本输入/输出；Gemini Live 的临时和确认文本以 `partial_response` 事件持续输出。
- 文件、音频、PCM 分片及 TTS 响应流一律显示为 `<binary omitted>`，仅附带内容类型、字节数和流标记，不受文本日志开关影响。
- API Key、Bearer Token 与常见认证字段由异常日志脱敏逻辑过滤。AI 文本本身仍可能包含业务敏感信息，生产环境应根据数据分级关闭正文或缩短截断长度。

*   **`system_instruction` (系统指令 / 人设注入)**
    *   **作用**：这是 Gemini 语音生成最强大的特性！你可以通过文本规定它“带有什么样的情绪去朗读”。
    *   **示例值**：
        *   *"你现在是一个深夜电台主持人，请用极度温柔、缓慢且充满磁性的语气朗读。"*
        *   *"你是一个激情的体育解说员，请用非常高昂、语速极快的语调朗读。"*
    *   *配置方法*：在 `GenerateContentConfig(system_instruction="...")` 中配置。

*   **`temperature` (创造力/随机性)**
    *   **作用**：控制语音表现力的丰富度。
    *   **推荐设置**：默认通常是 `0.7`。如果希望语气极其稳定保守，设为 `0.2`；如果希望它在朗读时自己加一些呼吸声、笑声或语气词（如“呃”、“啊哈”），可以调高到 `1.0` 以上。

---

## 2. 语音转写文字 (STT) 增强参数
**核心模型**：`gemini-3.6-flash` / `gemini-3.5-flash`

### 可配置参数：
*   **`temperature` (严谨度控制) —— 🔴 STT 必配**
    *   **作用**：语音转写最怕的是“AI 幻觉”（也就是你没说，它自己脑补了下半句）。
    *   **推荐设置**：强烈建议在 STT 的 `config` 中将 `temperature=0.0`。这会迫使大模型收起创造力，100% 像个无情的打字员一样只转录它听到的内容。

*   **Prompt 领域词汇增强 (Vocabulary/Context)**
    *   **作用**：在 Prompt 中塞入你的行业黑话，大幅提高专有名词准确率。
    *   **示例提示词**：*"请精准转录音频。背景信息：这是一段关于 IT 编程的对话，请注意识别以下专有名词：FastAPI, Vue3, Gemini, Kubernetes。"*

*   **`response_schema` (结构化输出 JSON)**
    *   **作用**：如果你不仅仅想要纯文本，还想要大模型帮你顺便“总结”音频。
    *   **示例用法**：强制模型返回 JSON，例如 `{"transcript": "完整原文", "summary": "一句话总结", "sentiment": "用户的情绪是高兴还是愤怒"}`。

---

## 3. 实时语音转录参数

**首选路径**：桌面 Microsoft Edge 87+ 的 `SpeechRecognition`，语言为 `zh-CN`。当前中文不强制本地模型，实际识别由 Edge 的远程服务完成，不调用本项目后端。

浏览器类型和 `SpeechRecognition` 构造器在页面加载时同步检测；非桌面 Edge、版本过低或 API 不可用时，点击录音会直接建立后端 `/api/stt/stream` WebSocket，不等待浏览器识别超时。

本地运行时以 `backend/.env` 为当前项目配置来源。若终端中残留另一个同名 `GEMINI_API_KEY`，项目文件中的值会覆盖它；部署环境没有 `backend/.env` 时，仍使用容器或系统注入的环境变量。

务必配置 `API_ACCESS_TOKEN`。所有 `/api` HTTP 与 `/api/stt/stream` WebSocket 都要求携带同一 Token；未配置时接口返回 503。

**后端实时路径**：`gemini-3.5-transcribe-live`

版本、构造器、安全上下文或运行时服务检查失败时，项目使用专用实时 STT 模型，通过 `input_audio_transcription` 接收增量和最终转录文本。

聚合平台当前不能处理此实时增量协议，因此两种启动模式的后端实时阶段都使用 Gemini Live。Live 失败后，服务端将已有 PCM 缓冲封装为 WAV，再交给当前启动模式对应的批量 STT；不会临时切换到另一种模式。

### 可配置参数：
*   **`language_codes=["cmn-Hans-CN"]`**
    *   固定为简体普通话，避免短句被自动语言检测误判为印地语或其他语言。
*   **`mode="SMART"`**
    *   清理口头填充词、重复和自我修正，并补充适当标点。
*   **`custom_vocabulary`**
    *   可添加业务专有名词以增强识别；建议仅加入真正容易误识别的词汇。
*   **PCM 输入**
    *   优先使用 16kHz、单声道、16-bit little-endian PCM，每约 100ms 发送一个分片。
## Gemini 网络与代理配置

Google Gen AI SDK 默认读取系统及 `HTTP_PROXY`、`HTTPS_PROXY` 环境配置。交互式语音请求建议使用：

- `GEMINI_HTTP_TIMEOUT_MS=30000`：单次请求超时。
- `GEMINI_HTTP_RETRY_ATTEMPTS=3`：包含首次请求在内的尝试次数。
- `GEMINI_TRUST_ENV=true`：允许 SDK 使用系统代理。
- `GEMINI_PROXY=http://127.0.0.1:7890`：可选的固定代理地址；配置后优先使用该地址。
- `API_ACCESS_TOKEN=your-secret`：后端接口访问 Token。HTTP 使用 `Authorization: Bearer ...` 或 `X-API-Token`；WebSocket 使用 `?token=`。

若日志出现 `httpcore._sync.http_proxy` 和 `Server disconnected without sending a response`，说明断开发生在代理传输层，不是模型拒绝。应确认代理进程稳定，或在网络允许直连时设置 `GEMINI_TRUST_ENV=false`。
