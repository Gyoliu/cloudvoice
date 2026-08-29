# 系统架构设计（Edge TTS + Gemini 降级架构）

## 1. 整体架构图

```mermaid
graph LR
    subgraph Web Frontend (Vanilla HTML+JS)
        UI_TTS[文字转语音模块]
        UI_STT_Upload[音频文件上传模块]
        UI_STT_Realtime[麦克风实时录音模块]
    end

    subgraph Backend API (FastAPI)
        API_TTS[HTTP POST /api/tts]
        API_TTS_Stream[HTTP POST /api/tts/stream]
        API_Upload[HTTP POST /api/stt/upload]
        API_WS[WebSocket /api/stt/stream]
    end

    subgraph Microsoft Edge Speech
        TTS_Primary[edge-tts / zh-CN-YunxiNeural]
        STT_Browser[SpeechRecognition / zh-CN]
    end

    subgraph Google AI Studio
        TTS_Fallback[Gemini TTS 模型组]
        STT_Primary[gemini-3.6-flash]
        STT_Fallback[gemini-3.5-flash]
        STT_Live[gemini-3.5-transcribe-live / cmn-Hans-CN]
    end

    UI_TTS -- 发送文本 JSON --> API_TTS
    UI_TTS -- Edge-only 流式播放 --> API_TTS_Stream
    API_TTS -- 首选生成 MP3 --> TTS_Primary
    API_TTS -- Edge 失败后生成 WAV --> TTS_Fallback
    TTS_Primary & TTS_Fallback --> API_TTS
    API_TTS_Stream -- MP3 分片直接透传，不降级 --> TTS_Primary
    TTS_Primary -- HTTP 分块响应 --> API_TTS_Stream
    
    UI_STT_Upload -- 提交音频文件 FormData --> API_Upload
    API_Upload -- 首选失败则降级 --> STT_Primary & STT_Fallback
    STT_Primary & STT_Fallback -- 返回文本转录结果 --> API_Upload
    
    UI_STT_Realtime -- 桌面 Edge 87+ 且运行可用，不调用项目接口 --> STT_Browser
    UI_STT_Realtime -- 不支持、策略禁用或运行失败 --> API_WS
    UI_STT_Realtime -- 后端路径持续发送 16-bit PCM 分片 --> API_WS
    API_WS -- 简体普通话实时转录 --> STT_Live
    API_WS -- Live API 失败则将 PCM 缓冲封装 WAV --> STT_Primary & STT_Fallback
```

## 2. 方案优势

1. **TTS 响应更快**：日常文字合成优先走 Edge TTS，固定使用 `zh-CN-YunxiNeural` 中文男声，并直接返回 MP3。
2. **自动容错**：Edge TTS 连接、接收或音频解析失败时，后端自动切换到 Gemini TTS 模型组并返回 WAV。
3. **接口兼容**：前端仍使用同一个 `/api/tts` 接口和 Blob 播放逻辑，无需关心实际音频提供方。
4. **低首播延迟**：`/api/tts/stream` 将 Edge TTS 的 MP3 分片直接交给浏览器 MediaSource，在完整语音生成前即可开始播放。
5. **减少后端 STT 调用**：可用的桌面 Edge 直接通过浏览器 SpeechRecognition 识别；版本、能力与运行时三层检查失败后才启用现有 WebSocket 链路。
