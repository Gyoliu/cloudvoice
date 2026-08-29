# 后端 API 接口规范 (API Specification)

**Base URL**: `http://localhost:8000/api`

---

## 1. 文字转语音 (TTS)
- **Endpoint**: `/tts`
- **Method**: `POST`
- **Description**: 接收前端传来的文本，优先使用 Microsoft Edge TTS 的 `zh-CN-YunxiNeural` 中文男声；失败时自动降级至 Google Gemini TTS。

**Request (JSON)**
```json
{
  "text": "你好，这是一段测试文本。",
  "voice_name": "Charon"
}
```
`voice_name` 仅用于 Gemini 降级路径，可选值：`Aoede`、`Puck`、`Charon`、`Kore`、`Fenrir`、`Leda`，默认使用男声 `Charon`。Edge TTS 首选音色固定为 `zh-CN-YunxiNeural`。

**Response**: Edge TTS 成功时返回 `audio/mpeg`，降级至 Gemini 时返回 `audio/wav`。响应头 `X-TTS-Provider` 的值为 `microsoft-edge` 或 `google-gemini`，前端可直接利用 Blob URL 播放。

### 1.1 Edge TTS 流式播放

- **Endpoint**: `/tts/stream`
- **Method**: `POST`
- **Description**: 仅使用 Microsoft Edge TTS 的 `zh-CN-YunxiNeural` 中文男声。后端取得首个 MP3 分片后返回响应，后续分片持续通过 HTTP 响应体发送；不会调用 Gemini。

**Request (JSON)**
```json
{
  "text": "你好，这是一段流式播放测试。"
}
```

**Response**: `audio/mpeg` 分块响应，包含以下响应头：

- `X-TTS-Provider: microsoft-edge`
- `Cache-Control: no-store`
- `X-Accel-Buffering: no`

首个音频分片生成前失败时返回 `502`。响应已经开始后发生的上游中断会表现为音频流提前结束，不会切换至 Google。

---

## 2. 语音转文字 - 文件上传 (STT Upload)
- **Endpoint**: `/stt/upload`
- **Method**: `POST`
- **Description**: 用于处理用户选择本地音频文件上传的场景。后端按真实 MIME 类型保存临时文件，并提交给 Gemini 3.6 Flash，失败时降级至 Gemini 3.5 Flash。

**Request (FormData)**
- `file`: 本地音频文件

支持常见的 AAC、FLAC、M4A、MP3、MP4、OGG、WAV 和 WebM 音频 MIME 类型。

**Response (JSON)**
```json
{
  "status": "success",
  "data": { "text": "完整音频转录出的文字内容" }
}
```

**Error**:
- `415`: 文件 MIME 类型不受支持。
- `502`: Gemini 语音识别服务暂时不可用。

---

## 3. 语音转文字 - 实时录音 (STT Real-time)
- **Endpoint**: `/stt/stream`
- **Protocol**: `WebSocket` (ws://localhost:8000/api/stt/stream)
- **Description**: 该接口是实时 STT 的兼容降级路径。桌面 Microsoft Edge 87 及以上且 `SpeechRecognition` 可正常启动时，前端直接使用浏览器识别，不建立 WebSocket。其他浏览器、旧版 Edge、企业策略禁用、网络服务或中文识别不可用时，前端自动进入本接口。

**前端选择流程**:
1. 检查是否为受支持的桌面 Edge、主版本是否不低于 87、页面是否处于安全上下文，并检查 `SpeechRecognition` 构造器。
2. 满足条件时以 `lang="zh-CN"`、`continuous=true`、`interimResults=true` 启动浏览器识别。
3. 浏览器识别成功后，不调用本项目的任何 STT 接口；长时间静音导致会话结束时自动继续监听。
4. 遇到 `network`、`service-not-allowed`、`language-not-supported` 或 `language-unavailable` 时，释放浏览器识别并自动切换后端 WebSocket。
5. 麦克风权限被拒绝或设备无音频输入时直接提示用户，避免重复申请同一权限。

Edge 当前中文识别使用浏览器提供的远程服务而非本地模型，音频可能发送至 Microsoft Azure。文件上传 STT 不使用 SpeechRecognition，仍调用 `/stt/upload`。

**后端降级路径**: 前端通过 `AudioWorklet` 采集 16kHz 单声道 16-bit PCM，每约 100ms 发送一个二进制分片；缺少 AudioWorklet 时使用兼容采集路径。后端将分片发送给 `gemini-3.5-transcribe-live`，使用 `cmn-Hans-CN` 固定简体普通话识别，同时保留同源 PCM 缓冲；Live API 失败时将缓冲封装成 WAV 并自动切换到批处理识别。
  
**后端交互流程**:
1. 前端连接 WebSocket，地址根据页面协议、主机和可选的 `google-voice-api-origin` 配置生成。
2. 前端发送初始化消息：`{"action":"start","sample_rate":16000}`；旧浏览器可能上报设备实际采样率。
3. 前端循环发送原始 PCM 二进制分片。
4. 停止时，前端先刷新最后一个 PCM 分片，再发送：`{"action":"stop"}`。
5. 后端返回中间或最终结果：
```json
{
  "is_final": false,
  "phase": "interim",
  "text": "目前识别到的部分文字..."
}
```

最终结果的 `is_final` 为 `true`；失败时返回通用的 `error` 字段，不暴露 SDK 内部异常。
