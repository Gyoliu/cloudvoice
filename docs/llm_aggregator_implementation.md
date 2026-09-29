# LLM 聚合平台语音能力实施方案

## 1. 结论

项目采用启动时固定提供方的策略。默认 `AI_PROVIDER_MODE=original`，需要聚合平台时显式改为 `aggregator`：

```text
original：普通 TTS 为 Edge → Gemini；流式 TTS 为 Edge；批量 STT 为 Gemini 主备模型
aggregator：普通/流式 TTS 与批量 STT 均只使用聚合平台
实时 STT：Edge SpeechRecognition → Gemini Live；失败后的缓冲批处理遵循启动模式
```

两种模式不会因为单次请求失败而互相切换。原有模式内部仍保留 Edge → Gemini、Gemini 主备模型等既有稳定性逻辑。

## 2. 聚合平台 TTS 流式能力核验

### 2.1 文档与源码结论

FreeLLMAPI 文档中的 `Streaming` 示例属于 `/v1/chat/completions` 的 SSE 文本流。媒体章节声明 `/v1/audio/speech` 返回二进制音频，但没有声明 TTS 的 SSE 音频增量协议。

当前开源实现的 `SpeechRequest` 只包含：

- `model`
- `input`
- `voice`
- `response_format`

路由会先等待 `runSpeech(...)` 返回完整音频，再发送响应体。因此当前能力属于“完整合成后的二进制 HTTP 响应”，不是 Gemini 3.8 在生成阶段返回的 `audio/l16` 增量事件。

### 2.2 部署平台实测

对 `https://gyo.ccwu.cc/v1` 的公开 OpenAPI 和真实请求核验结果：

| 测试项 | 结果 |
|---|---|
| OpenAPI `SpeechRequest` | 没有 `stream`、`stream_format` 字段 |
| `model=gemini-3.8-flash-tts` | HTTP 400，音频模型 ID 未暴露给该端点 |
| `model=auto:fast` | HTTP 400 |
| `model=auto` | HTTP 200 |
| `stream=true` | 请求成功，但返回完整 `Content-Length`，字段被忽略 |
| `stream=true, stream_format=sse` | 仍返回普通二进制音频，不返回 SSE |
| 实际路由 | 本次响应头为 `X-Provider: cloudflare` |
| 声明格式 | `Content-Type: audio/mpeg` |
| 实际文件签名 | `RIFF/WAVE` |
| 仅发送标准 `input` 字段 | HTTP 400，Cloudflare 路由报告缺少 `text` |
| 同时发送 `input` 与兼容 `text` 字段 | HTTP 200，返回 RIFF/WAVE 音频 |

短文本集成测试中，项目后端从聚合平台取得首个响应分片约需 2.49 秒，完整接收约需 3.18 秒。另一次带 `stream=true` 的协议测试首字节约 1.80 秒、完整接收约 2.34 秒；这属于请求波动，不能证明 `stream` 参数生效。

2026-09-28 再次实测发现，平台公开请求模型仍以 `input` 为标准字段，但当前 Cloudflare 音频适配器内部读取 `text`。项目请求会同时发送内容相同的 `input` 和 `text`：前者保留 OpenAI 兼容契约，后者兼容当前部署平台的适配器。兼容请求实测返回 HTTP 200；若平台后续修复字段映射，多余字段不影响现有调用。

### 2.3 当前实现语义

当前 `/api/tts/stream` 使用流式 HTTP 客户端读取聚合平台响应，并立即向浏览器转发，不在本项目后端再次缓存完整音频。这样可以减少本项目自身的二次等待和内存复制，但首包时间仍包含聚合平台完成上游合成的时间。

浏览器按真实文件签名处理：

- WAV：解析 RIFF/fmt/data，使用 Web Audio API 按 PCM 帧连续调度播放。
- MP3：使用 MediaSource 追加并播放。

聚合平台在首个有效音频分片前失败时直接返回 `502`，不会切换 Edge。响应已经开始后发生故障时，当前 HTTP 音频流会提前结束。

## 3. 模型路由策略

聚合平台音频端点当前必须使用 `model=auto`。程序会：

1. 将 TTS 配置中的 `auto:fast` 规范为 `auto`。
2. 若显式模型 ID 被平台以 HTTP 400 拒绝，在聚合平台内部再试一次 `auto`。
3. 平台内部 `auto` 仍失败时直接结束请求，不进入 Edge / Google SDK 链路。

这能确保 `aggregator` 模式始终由聚合平台负责，但不能从客户端强制本次 `auto` 一定命中 Gemini 3.8。若必须固定 Gemini 3.8，需要先在聚合平台 Audio 模型管理中确认它已启用，并取得该音频目录实际接受的 provider model id；当前公开名称 `gemini-3.8-flash-tts` 已实测不可直接调用。

## 4. 后续真正生成期流式的升级点

当聚合平台正式提供 TTS 流式协议后，按其契约升级 `LLMAggregatorClient.stream_speech`：

1. 原始音频分块：直接透传，并使用上游声明或首块元数据确定 PCM 参数。
2. SSE：解析 `speech.audio.delta` 等事件中的 Base64 音频数据，忽略 keepalive 和结束事件。
3. 上游为无头 `audio/l16` 时：向浏览器传递采样率/声道/位深响应头，前端复用 Web Audio 调度器播放。
4. 保持首块前失败返回错误、首块后结束当前流的边界。

在平台发布该契约前，不把 `stream=true` 当作已经生效的保证，也不在 `aggregator` 模式绕过聚合平台调用 Google。

## 5. 配置

```dotenv
AI_PROVIDER_MODE=aggregator
LLM_AGGREGATOR_BASE_URL=https://gyo.ccwu.cc/v1
LLM_AGGREGATOR_API_KEY=$YOUR_KEY
LLM_AGGREGATOR_TTS_MODEL=auto
LLM_AGGREGATOR_TTS_VOICE=zh-CN-YunxiNeural
LLM_AGGREGATOR_STT_MODEL=auto:fast
LLM_AGGREGATOR_CONNECT_TIMEOUT_SECONDS=3
LLM_AGGREGATOR_TTS_TIMEOUT_SECONDS=20
LLM_AGGREGATOR_STT_TIMEOUT_SECONDS=45
```

生产环境默认建议使用 `AI_PROVIDER_MODE=original`。只有需要聚合平台独占处理时才改为 `aggregator`，并在修改后重启服务。部署环境中即使暂时保留 `LLM_AGGREGATOR_TTS_MODEL=auto:fast`，应用也会自动规范为 `auto`；仍建议直接改成 `auto`，使配置与平台真实能力一致。
