# Gemini API 与提供方选择方案

## 1. 方案定位

项目默认使用原有稳定模式，通过 `AI_PROVIDER_MODE=original` 保留 Microsoft Edge 与 Google Gemini SDK 实现。需要统一聚合平台时，启动前改为 `AI_PROVIDER_MODE=aggregator`。两种模式互斥，不采用聚合平台失败后自动切换 SDK 的熔断策略。

## 2. 启动模式

### `original`（默认推荐）

- 普通 TTS：Microsoft Edge TTS → Google Gemini SDK。
- 流式 TTS：Microsoft Edge TTS。
- 批量 STT：`gemini-3.6-flash` → `gemini-3.5-flash`。
- Google TTS：优先 `gemini-3.8-flash-tts` Interactions API，再尝试兼容模型。

这些降级只发生在原有实现内部，用于延续已经验证过的稳定性。

### `aggregator`

- 普通与流式 TTS：只调用 `/v1/audio/speech`。
- 批量与缓冲 STT：只调用 `/v1/audio/transcriptions`。
- 平台发生连接、超时、HTTP 或格式错误时直接向接口返回失败，不调用 Edge 或 Gemini 批处理 SDK。

聚合平台当前不提供本项目所需的实时增量 STT，因此后端实时录音仍使用 Gemini Live；Live 失败后的完整缓冲识别会遵循所选启动模式。

## 3. 配置示例

```dotenv
# 默认稳定模式
AI_PROVIDER_MODE=original

# 如需聚合平台独占模式，改成 aggregator 并重启服务
LLM_AGGREGATOR_BASE_URL=https://gyo.ccwu.cc/v1
LLM_AGGREGATOR_API_KEY=$YOUR_KEY
```

`AI_PROVIDER_MODE` 只在进程启动时读取。修改后必须重启服务，不提供请求级或前端动态切换。

## 4. 隐私注意事项

无论选择哪个云端提供方，都不应提交公司机密、客户隐私、财务数据或其他敏感信息。使用 Google 免费层级时，还应单独核对当前 Google 服务条款及数据使用政策。
