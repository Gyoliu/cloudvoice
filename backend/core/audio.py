import io
import wave


def pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """将原始 PCM 音频封装为标准 WAV 字节流。"""
    if sample_rate <= 0:
        raise ValueError("音频采样率必须大于 0")
    if channels <= 0:
        raise ValueError("音频声道数必须大于 0")
    if sample_width <= 0:
        raise ValueError("音频采样位宽必须大于 0")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return output.getvalue()
