# 系统架构设计 (纯 AI Studio / Gemini 统一架构)

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
        API_Upload[HTTP POST /api/stt/upload]
        API_WS[WebSocket /api/stt/stream]
    end

    subgraph Google AI Studio (Free Tier)
        TTS_Primary[gemini-3.1-flash-tts-preview]
        TTS_Fallback[gemini-2.5-flash-preview-tts]
        STT_Primary[gemini-3.6-flash]
        STT_Fallback[gemini-3.5-flash]
    end

    UI_TTS -- 发送文本 JSON --> API_TTS
    API_TTS -- 首选失败则降级 --> TTS_Primary & TTS_Fallback
    TTS_Primary & TTS_Fallback -- 返回裸 PCM，后端封装 WAV --> API_TTS
    
    UI_STT_Upload -- 提交音频文件 FormData --> API_Upload
    API_Upload -- 首选失败则降级 --> STT_Primary & STT_Fallback
    STT_Primary & STT_Fallback -- 返回文本转录结果 --> API_Upload
    
    UI_STT_Realtime -- 持续发送 16-bit PCM 分片 --> API_WS
    API_WS -- Live API 失败则将 PCM 缓冲封装 WAV --> STT_Primary & STT_Fallback
```

## 2. 方案优势 (Pure AI Studio)

1. **极致简单的部署**：抛弃了复杂的 Google Cloud Platform (GCP) 的 IAM 和服务账号，**整个项目仅需 1 个 Gemini API Key** 即可全部跑通。
2. **纯正的 AI 体验**：使用 Gemini 2.5 自带的语音合成，声音具有更强的情感和呼吸感，适合做对话机器人。
3. **完全免费**：利用 AI Studio 的免费额度，对于个人开发和测试来说，音频处理没有任何额外成本。
