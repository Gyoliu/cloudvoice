from typing import Literal

from pydantic import BaseModel, Field

VoiceName = Literal["Aoede", "Puck", "Charon", "Kore", "Fenrir", "Leda"]


class TTSRequest(BaseModel):
    # 需要转换为语音的原始文本；暂不设置长度上限。
    text: str = Field(min_length=1, description="需要转换为语音的文本")
    # Gemini 预置音色名称，只允许使用后端已验证的音色。
    voice_name: VoiceName = Field(default="Kore", description="Gemini 预置音色名称")
