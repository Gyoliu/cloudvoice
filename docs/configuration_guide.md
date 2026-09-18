# 核心模型增强与个性化参数配置指南

项目的 TTS 优先使用 Microsoft Edge TTS，STT 与 TTS 降级路径使用 Gemini。下列参数用于说明各路径的可配置能力。

---

## 1. 文字转语音 (TTS) 个性化参数
**首选服务**：Microsoft Edge TTS，固定中文男声 `zh-CN-YunxiNeural`，输出 MP3。

**降级服务**：Google Gemini TTS 模型组，输出 WAV。

**流式播放**：`POST /api/tts/stream` 仅使用 Edge TTS。浏览器通过 MediaSource 逐块追加 `audio/mpeg` 数据；Edge 失败时结束请求，不调用 Gemini。

### 可配置参数：
*   **`voice_name`（Gemini 降级音色）**
    *   **作用**：仅在 Edge TTS 失败并切换到 Gemini 后决定播报员的基础音色，默认 `Charon` 男声。
    *   **可选值**：`Aoede` (温和女声)、`Puck` (活力男声)、`Charon` (沉稳男声)、`Kore` (清脆女声)、`Fenrir` (粗犷男声)、`Leda` (知性女声)。
    *   *配置方法*：通过 `/api/tts` 请求字段传入；正常 Edge 路径始终使用 `zh-CN-YunxiNeural`。

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

**降级路径**：`gemini-3.5-transcribe-live`

版本、构造器、安全上下文或运行时服务检查失败时，项目使用专用实时 STT 模型，通过 `input_audio_transcription` 接收增量和最终转录文本。

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
