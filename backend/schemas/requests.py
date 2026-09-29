from typing import Literal

from pydantic import BaseModel, Field

VoiceName = Literal["Aoede", "Puck", "Charon", "Kore", "Fenrir", "Leda"]


class TTSRequest(BaseModel):
    # 需要转换为语音的原始文本；暂不设置长度上限。
    text: str = Field(min_length=1, description="需要转换为语音的文本")
    # 仅在 original 模式下 Edge 失败并进入 Gemini 时生效；默认选择沉稳男声。
    voice_name: VoiceName = Field(default="Charon", description="原有模式 Gemini 音色名称")


class TTSStreamRequest(BaseModel):
    # 交给启动时所选提供方的流式播放文本；按当前产品约定暂不设置长度上限。
    text: str = Field(min_length=1, description="需要流式转换为语音的文本")
