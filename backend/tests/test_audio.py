import io
import wave

from core.audio import pcm_to_wav


def test_pcm_to_wav_writes_valid_header_and_frames():
    """PCM 封装后应保留采样参数和全部音频帧。"""
    pcm_data = b"\x00\x00\xff\x7f\x00\x80"

    wav_data = pcm_to_wav(pcm_data, sample_rate=16_000)

    with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == pcm_data
