# 后端 API 接口规范 (API Specification)

**Base URL**: `http://localhost:8000/api`

---

## 1. 文字转语音 (TTS)
- **Endpoint**: `/tts`
- **Method**: `POST`
- **Description**: 接收前端传来的文本，调用 Gemini TTS 生成 WAV 音频。

**Request (JSON)**
```json
{
  "text": "你好，这是一段测试文本。",
  "voice_name": "Kore"
}
```
`voice_name` 可选值：`Aoede`、`Puck`、`Charon`、`Kore`、`Fenrir`、`Leda`，默认 `Kore`。

**Response**: `audio/wav` 二进制流，前端直接利用 Blob URL 播放。

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
- **Description**: 前端优先通过 `AudioWorklet` 采集单声道 16-bit PCM，每约 100ms 发送一个二进制分片；缺少 AudioWorklet 时使用兼容采集路径。后端将分片发送给 Gemini Live API，同时保留同源 PCM 缓冲；Live API 失败时将缓冲封装成 WAV 并自动切换到批处理识别。
  
**交互流程**:
1. 前端连接 WebSocket，地址根据页面协议、主机和可选的 `google-voice-api-origin` 配置生成。
2. 前端发送初始化消息：`{"action":"start","sample_rate":48000}`。
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
