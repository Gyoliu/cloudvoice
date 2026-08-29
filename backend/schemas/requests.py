from typing import Literal

from pydantic import BaseModel, Field

VoiceName = Literal["Aoede", "Puck", "Charon", "Kore", "Fenrir", "Leda"]


class TTSRequest(BaseModel):
    # 需要转换为语音的原始文本；暂不设置长度上限。
    text: str = Field(min_length=1, description="需要转换为语音的文本")
    # 仅在 Edge TTS 失败后用于 Gemini 降级；默认选择沉稳男声。
    voice_name: VoiceName = Field(default="Charon", description="Gemini 降级音色名称")


class EdgeTTSStreamRequest(BaseModel):
    # Edge-only 流式播放文本；按当前产品约定暂不设置长度上限。
    text: str = Field(min_length=1, description="需要流式转换为语音的文本")
