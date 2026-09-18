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
| Edge TTS 流式播放 | `POST` | `/api/tts/stream` | `application/json` | `audio/mpeg` 分块响应 |
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

优先调用 Microsoft Edge TTS，固定使用中文男声 `zh-CN-YunxiNeural` 并返回 MP3。Edge 调用失败时，自动切换 Google Gemini TTS 并返回 WAV。

### 2.1 请求

`Content-Type: application/json`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `text` | string | 是 | - | 需要朗读的文本，至少 1 个字符 |
| `voice_name` | enum | 否 | `Charon` | 仅用于 Gemini 降级路径 |

`voice_name` 可选值：`Aoede`、`Puck`、`Charon`、`Kore`、`Fenrir`、`Leda`。正常 Edge 路径忽略该字段。

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
| `502` | Edge 和 Gemini 均生成失败 | `语音生成服务暂时不可用` |

## 3. Edge TTS 流式播放

### `POST /api/tts/stream`

仅使用 Microsoft Edge TTS 和 `zh-CN-YunxiNeural`。服务端取得首个 MP3 分片后发送响应头，后续音频通过同一个 HTTP 响应体持续传输，不会降级到 Gemini。

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
| `Content-Type` | `audio/mpeg` | MP3 音频分片 |
| `X-TTS-Provider` | `microsoft-edge` | 固定提供方 |
| `Cache-Control` | `no-store` | 禁止缓存 |
| `X-Accel-Buffering` | `no` | 提示 Nginx 不缓冲响应 |

浏览器前端使用 `MediaSource` 和 `SourceBuffer("audio/mpeg")` 追加分片。调用前应检查 `MediaSource.isTypeSupported("audio/mpeg")`；不支持该能力的浏览器不能使用项目内置的流式播放器。

### 3.3 错误与中断语义

| 场景 | 行为 |
|---|---|
| 首个音频分片产生前失败 | 返回 `502`，`detail` 为 `Edge 流式语音服务暂时不可用` |
| 响应已经开始后 Edge 中断 | HTTP 音频流提前结束，无法再改写为 JSON 错误 |
| 客户端取消请求 | 后端关闭 Edge 上游异步生成器，不调用 Gemini |
| 请求体不合法 | 返回 `422` |

## 4. 上传音频转文字

### `POST /api/stt/upload`

接收一个音频文件，保存为临时文件并上传至 Gemini。当前依次尝试 `gemini-3.6-flash` 和 `gemini-3.5-flash`，成功后返回纯转录文本，并尝试删除 Gemini 远端临时文件。

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
5. 持续认证失败时，Live 和批处理共用同一个 Gemini 凭证，服务端直接返回凭证错误，不执行无意义的批处理调用。
6. 停止录音后最多等待 Live 最终结果 10 秒；超时后使用已经确认的片段。

## 6. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `API_ACCESS_TOKEN` | 无 | 后端 `/api` 访问 Token；未配置时所有 `/api` 返回 503 |
| `GEMINI_API_KEY` | 无 | Gemini API 凭证；STT 和 Gemini TTS 降级共用 |
| `GEMINI_HTTP_TIMEOUT_MS` | `30000` | 普通 Gemini HTTP 请求超时，单位毫秒 |
| `GEMINI_HTTP_RETRY_ATTEMPTS` | `3` | 普通 HTTP 请求总尝试次数，包含首次请求 |
| `GEMINI_TRUST_ENV` | `true` | 是否读取系统代理环境变量 |
| `GEMINI_PROXY` | 空 | 可选固定代理地址 |

本地存在 `backend/.env` 时，其中的配置覆盖终端遗留同名变量；部署环境没有该文件时，使用容器或系统注入的环境变量。Live WebSocket 的快速重连次数和间隔当前是代码常量，不受上述 HTTP 重试配置控制。

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
