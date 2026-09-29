# Google Voice 后端接口文档

本文档以当前后端源码为准，覆盖普通 HTTP、HTTP 流式响应和 WebSocket 实时转录协议。

## 1. 基本信息

| 项目 | 本地开发值 |
|---|---|
| HTTP API Base URL | `http://localhost:8000/api` |
| WebSocket Base URL | `ws://localhost:8000/api` |
| Swagger UI | `http://localhost:8000/docs` |
| OpenAPI JSON | `http://localhost:8000/openapi.json` |

生产环境使用 HTTPS 时，WebSocket 协议必须同步改为 `wss://`。

### 1.1 接口一览

| 功能 | 方法 | 路径 | 请求类型 | 成功响应 |
|---|---|---|---|---|
| 普通文字转语音 | `POST` | `/api/tts` | `application/json` | `audio/mpeg` 或 `audio/wav` |
| TTS 流式播放 | `POST` | `/api/tts/stream` | `application/json` | `audio/wav` 或 `audio/mpeg` 分块响应 |
| 上传音频转文字 | `POST` | `/api/stt/upload` | `multipart/form-data` | JSON |
| 实时录音转文字 | `WebSocket` | `/api/stt/stream` | JSON 控制帧 + PCM 二进制帧 | JSON 消息流 |

### 1.2 通用错误格式

业务处理失败使用 FastAPI 的标准 HTTP 错误体：

```json
{
  "detail": "语音生成服务暂时不可用"
}
```

请求体校验失败返回 `422 Unprocessable Entity`：

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "text"],
      "msg": "String should have at least 1 character",
      "input": ""
    }
  ]
}
```

除音频二进制响应外，响应字符编码均为 UTF-8。服务端不会向调用方返回 Gemini 或 Edge SDK 的内部异常详情。

## 2. 普通文字转语音

### `POST /api/tts`

调用链由进程启动时的 `AI_PROVIDER_MODE` 决定。默认 `original` 使用 Microsoft Edge TTS，失败后进入 Google Gemini SDK；`aggregator` 只调用 LLM 聚合平台 `/v1/audio/speech`，失败时不切换原有链路。

### 2.1 请求

`Content-Type: application/json`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `text` | string | 是 | - | 需要朗读的文本，至少 1 个字符 |
| `voice_name` | enum | 否 | `Charon` | 仅用于 original 模式的 Google Gemini SDK 路径 |

`voice_name` 可选值：`Aoede`、`Puck`、`Charon`、`Kore`、`Fenrir`、`Leda`。聚合平台与 Edge 路径忽略该字段，使用部署配置中的 `zh-CN-YunxiNeural`。

```json
{
  "text": "你好，这是一段测试文本。",
  "voice_name": "Charon"
}
```

### 2.2 成功响应

状态码：`200 OK`

| 实际提供方 | `Content-Type` | `X-TTS-Provider` | 响应体 |
|---|---|---|---|
| LLM 聚合平台 | 按文件签名确定，当前为 `audio/wav` | `llm-aggregator` | 完整音频文件 |
| Microsoft Edge | `audio/mpeg` | `microsoft-edge` | 完整 MP3 文件 |
| Google Gemini | `audio/wav` | `google-gemini` | 完整 WAV 文件 |

调用方必须根据响应 `Content-Type` 处理音频，不能固定假设为 MP3。

```bash
curl -X POST http://localhost:8000/api/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"你好","voice_name":"Charon"}' \
  --output speech.bin
```

### 2.3 错误响应

| 状态码 | 场景 | `detail` |
|---:|---|---|
| `422` | 缺少 `text`、空字符串或 `voice_name` 不在枚举中 | FastAPI 校验错误数组 |
| `502` | 当前启动模式的 TTS 调用失败 | `语音生成服务暂时不可用` |

## 3. TTS 流式播放

### `POST /api/tts/stream`

`original` 模式只使用 Microsoft Edge TTS 流；`aggregator` 模式只调用聚合平台 `/v1/audio/speech`。服务端在识别首批音频的真实格式后才发送响应头，后续音频通过同一个 HTTP 响应体持续传输。任一模式启动失败时直接返回 `502`，不会切换到另一模式。

当前聚合平台会先完成上游合成，再返回带 `Content-Length` 的二进制音频；其 `stream=true` 与 `stream_format=sse` 尚不构成正式 TTS 流式契约。因此本接口能避免本项目后端再次完整缓存，并让浏览器边下载边播放，但聚合平台路径的首包时间仍包含上游完整合成耗时。核验细节见 `docs/llm_aggregator_implementation.md`。

### 3.1 请求

`Content-Type: application/json`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `text` | string | 是 | 需要边生成边播放的文本，至少 1 个字符 |

```json
{
  "text": "你好，这是一段流式播放测试。"
}
```

### 3.2 成功响应

状态码：`200 OK`

| 响应头 | 值 | 说明 |
|---|---|---|
| `Content-Type` | `audio/wav` 或 `audio/mpeg` | 按音频魔数识别，不信任上游错误响应头 |
| `X-TTS-Provider` | `llm-aggregator` 或 `microsoft-edge` | 本次实际提供方 |
| `Cache-Control` | `no-store` | 禁止缓存 |
| `X-Accel-Buffering` | `no` | 提示 Nginx 不缓冲响应 |

浏览器对 MP3 使用 `MediaSource` 追加分片；对 16-bit PCM WAV 则解析 RIFF 文件头，并使用 Web Audio API 连续调度 PCM 分片。这样聚合平台即使错误声明 `audio/mpeg`、实际返回 WAV，仍能边接收边播放。

### 3.3 错误与中断语义

| 场景 | 行为 |
|---|---|
| 当前模式在首个分片前失败 | 返回 `502`，`detail` 为 `流式语音服务暂时不可用` |
| 响应已经开始后上游中断 | HTTP 音频流提前结束，无法混入另一种音频编码继续降级 |
| 客户端取消请求 | 后端关闭当前上游异步生成器和网络连接 |
| 请求体不合法 | 返回 `422` |

## 4. 上传音频转文字

### `POST /api/stt/upload`

接收一个音频文件并保存为临时文件。`original` 模式上传至 Gemini，并依次尝试 `gemini-3.6-flash` 和 `gemini-3.5-flash`；`aggregator` 模式只提交到聚合平台 `/v1/audio/transcriptions`。当前模式失败时不会切换另一模式。Gemini 路径完成后会尝试删除远端临时文件。

### 4.1 请求

`Content-Type: multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `file` | binary | 是 | 音频文件 |

支持的 MIME 类型：

| MIME 类型 | 临时文件后缀 |
|---|---|
| `audio/aac` | `.aac` |
| `audio/flac` | `.flac` |
| `audio/m4a`、`audio/mp4`、`audio/x-m4a` | `.m4a` |
| `audio/mp3`、`audio/mpeg` | `.mp3` |
| `audio/ogg` | `.ogg` |
| `audio/wav`、`audio/x-wav` | `.wav` |
| `audio/webm` | `.webm` |

MIME 参数会被忽略，例如 `audio/webm; codecs=opus` 会按 `audio/webm` 处理。

```bash
curl -X POST http://localhost:8000/api/stt/upload \
  -F 'file=@recording.wav;type=audio/wav'
```

### 4.2 成功响应

状态码：`200 OK`

```json
{
  "status": "success",
  "data": {
    "text": "完整音频转录出的文字内容"
  }
}
```

### 4.3 错误响应

| 状态码 | 场景 | `detail` |
|---:|---|---|
| `415` | `Content-Type` 不在支持列表中 | `仅支持常见音频文件格式` |
| `422` | 未提供 `file` 字段 | FastAPI 校验错误数组 |
| `502` | 上传、模型调用或响应解析失败 | `语音识别服务暂时不可用` |

## 5. 实时录音转文字

### `WebSocket /api/stt/stream`

完整地址：`ws://localhost:8000/api/stt/stream`

该接口用于不能使用 Edge 浏览器 `SpeechRecognition` 的客户端。桌面版 Microsoft Edge 87+ 且浏览器识别可用时，项目前端直接使用浏览器服务，不建立此 WebSocket。

### 5.1 前端路由选择

页面加载时同步执行以下检查：

1. 是否为桌面版 Microsoft Edge，而不是 Edge Android 或 Edge iOS。
2. Edge 主版本是否不低于 87。
3. 页面是否处于安全上下文。
4. 是否存在 `SpeechRecognition` 或 `webkitSpeechRecognition` 构造器。

全部满足时，前端使用：

```text
lang = zh-CN
continuous = true
interimResults = true
maxAlternatives = 1
```

Edge 浏览器识别成功后不调用本项目 STT 接口。遇到 `network`、`service-not-allowed`、`language-not-supported` 或 `language-unavailable` 时，前端释放 Edge 识别实例并切换本 WebSocket。非 Edge 浏览器直接连接本接口，不等待浏览器识别失败。

### 5.2 音频格式

| 属性 | 值 |
|---|---|
| 编码 | signed 16-bit PCM |
| 字节序 | little-endian |
| 声道 | 单声道 |
| 目标采样率 | 16000 Hz |
| 建议分片时长 | 约 100 ms |

浏览器无法创建 16kHz `AudioContext` 时，可以发送设备实际采样率，但必须在 `start` 控制消息中如实声明。

### 5.3 客户端发送协议

连接成功后，消息必须按以下顺序发送：

#### 第一步：开始控制帧

文本帧：

```json
{
  "action": "start",
  "sample_rate": 16000
}
```

`sample_rate` 必须是 `8000` 到 `96000` 之间的整数。

#### 第二步：PCM 音频帧

连续发送 WebSocket 二进制帧，每帧包含一段原始 PCM，不包含 WAV 文件头。

#### 第三步：停止控制帧

发送最后一个 PCM 分片后，再发送文本帧：

```json
{
  "action": "stop"
}
```

不支持其他 `action`，控制帧必须是合法 JSON 对象。

### 5.4 服务端消息协议

所有服务端消息均为 JSON 文本帧。

#### 状态消息

```json
{
  "is_final": false,
  "status": "已连接 Live API (gemini-3.5-transcribe-live)，请说话..."
}
```

可能出现的状态包括：

- `Live API 首次连接失败，正在快速重试...`
- `Live API 不可用，已降级为缓冲识别模式`
- `[降级模式] 持续接收中... (32000 字节)`
- `[降级模式] 录音结束，正在使用批处理模型解析...`

#### Live 中间结果

```json
{
  "is_final": false,
  "phase": "interim",
  "text": "目前识别到的部分文字"
}
```

`interim` 文本可能被后续结果修正，调用方应覆盖显示，而不是追加。

#### Live 已确认片段

```json
{
  "is_final": false,
  "phase": "confirmed",
  "text": "已经确认的累计文字"
}
```

#### 最终结果

```json
{
  "is_final": true,
  "text": "完整识别结果"
}
```

无音频时，最终 `text` 可能是 `未收到音频数据`；没有识别出内容时可能是 `未识别到语音内容`。

#### 错误结果

```json
{
  "is_final": true,
  "error": "Gemini 凭证无效，请更新 GEMINI_API_KEY 后重试"
}
```

其他错误文本包括：

- `实时转录协议错误`
- `语音识别服务暂时不可用`

收到 `is_final: true` 后，客户端应停止录音并关闭 WebSocket。服务端处理完成后也会主动关闭连接。

### 5.5 Live API 与降级规则

后端使用 `gemini-3.5-transcribe-live`，响应模态为文本，语言提示为 `cmn-Hans-CN`，转录模式为 `SMART`。

1. Live 握手发生连接重置、超时或临时认证失败时，250ms 后快速重连一次。
2. Live 会话正常工作时，服务端持续返回 `interim` 和 `confirmed` 文本，实现边录边输出。
3. 两次握手均失败，或者会话中途失败时，服务端使用已经保留的 PCM 缓冲进入批处理模式。
4. 缓冲模式只在收到 `stop` 后提交完整 WAV，因此不会边录边输出。
5. Live 认证失败时，`original` 模式直接返回 Gemini 凭证错误；`aggregator` 模式可使用独立平台凭证完成缓冲识别。
6. 停止录音后最多等待 Live 最终结果 10 秒；超时后使用已经确认的片段。

## 6. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `API_ACCESS_TOKEN` | 无 | 后端 `/api` 访问 Token；未配置时所有 `/api` 返回 503 |
| `AI_PROVIDER_MODE` | `original` | 启动提供方；仅允许 `original` 或 `aggregator`，修改后必须重启 |
| `AI_LOG_TEXT_CONTENT` | `true` | 是否记录 AI 文本输入输出；关闭后仅记录字符数 |
| `AI_LOG_TEXT_MAX_LENGTH` | `12000` | 单个文本日志最大字符数；`0` 表示不截断 |
| `GEMINI_API_KEY` | 无 | original 模式的 STT/TTS，以及两种模式共用的后端实时 STT |
| `LLM_AGGREGATOR_BASE_URL` | 空 | 聚合平台 API 根地址，例如 `https://gyo.ccwu.cc/v1` |
| `LLM_AGGREGATOR_API_KEY` | 空 | 聚合平台 Bearer Token |
| `LLM_AGGREGATOR_TTS_MODEL` | `auto` | TTS 路由模型；当前 `auto:fast` 会规范为 `auto` |
| `LLM_AGGREGATOR_TTS_VOICE` | `zh-CN-YunxiNeural` | 聚合平台 TTS 音色 |
| `LLM_AGGREGATOR_STT_MODEL` | `auto:fast` | 批量 STT 路由模型 |
| `LLM_AGGREGATOR_CONNECT_TIMEOUT_SECONDS` | `3` | 聚合平台连接超时，单位秒 |
| `LLM_AGGREGATOR_TTS_TIMEOUT_SECONDS` | `20` | TTS 相邻响应分片读取超时，单位秒 |
| `LLM_AGGREGATOR_STT_TIMEOUT_SECONDS` | `45` | STT 请求超时，单位秒 |
| `GEMINI_HTTP_TIMEOUT_MS` | `30000` | 普通 Gemini HTTP 请求超时，单位毫秒 |
| `GEMINI_HTTP_RETRY_ATTEMPTS` | `3` | 普通 HTTP 请求总尝试次数，包含首次请求 |
| `GEMINI_TRUST_ENV` | `true` | 是否读取系统代理环境变量 |
| `GEMINI_PROXY` | 空 | 可选固定代理地址 |

本地存在 `backend/.env` 时，其中的配置覆盖终端遗留同名变量；部署环境没有该文件时，使用容器或系统注入的环境变量。`AI_PROVIDER_MODE` 在进程启动时锁定，不会因单次请求失败自动改变；旧配置 `LLM_AGGREGATOR_ENABLED` 不再参与路由选择。Live WebSocket 的快速重连次数和间隔当前是代码常量，不受上述 HTTP 重试配置控制。

### 可观测日志

每个 HTTP 与 WebSocket 请求生成服务端 `request_id`。HTTP 响应同时返回 `X-Request-ID`；同一请求触发的 AI 调用通过日志中的 `request_id` 关联，各次模型尝试再以 `call_id` 区分。

- `API_REQUEST` 记录方法、路径、传输类型、状态和耗时，不记录查询参数、请求头或请求体。
- `AI_CALL` 的 `request`、`partial_response`、`response` 事件记录提供方、模型、文本输入/输出、状态与耗时。
- 音频、上传文件、PCM 分片及 TTS 文件流不记录内容，只记录 `<binary omitted>`、MIME 类型、字节数和流标记。
- 异常链会脱敏已配置的 Key 和常见认证字段。若业务文本含敏感数据，可设置 `AI_LOG_TEXT_CONTENT=false`。

### 认证方式

- HTTP：请求头 `Authorization: Bearer <token>`，或 `X-API-Token: <token>`
- WebSocket：连接 URL 查询参数 `?token=<token>`
- 静态前端页面（`/`、`/app.js` 等）不要求 Token

## 7. 当前限制与调用方责任

以下事项是当前实现的真实边界：

- 当前为共享静态 Token，不是多用户会话或 OAuth。
- 服务默认允许任意 CORS 来源，并可监听全部网络接口。
- TTS 文本、上传文件、WebSocket 音频缓冲当前没有大小上限。
- 上传接口信任客户端声明的 MIME 类型，没有读取文件签名验证真实格式。
- Edge 中文 `SpeechRecognition` 使用浏览器远程服务，不等同于离线识别。
- `/api/tts/stream` 一旦开始返回音频，后续故障只能表现为流提前结束。
- FastAPI 自动生成的 OpenAPI 只覆盖 HTTP 路由，不包含 WebSocket 消息协议；WebSocket 以本文档为准。

调用方不应将该服务直接暴露到不可信网络，也不应提交敏感或超出运行环境承载能力的数据。
