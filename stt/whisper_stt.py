"""
stt/whisper_stt.py
==================
Microphone se audio record karo aur Whisper se text mein convert karo.
Supports: Hindi, English, Hinglish (auto-detect)
"""

import whisper
import pyaudio
import wave
import tempfile
import os

# ── Config ────────────────────────────────────────────────────
WHISPER_MODEL  = "base"          # tiny/base/small/medium — base achha balance hai
SAMPLE_RATE    = 16000           # Whisper 16kHz chahta hai
CHANNELS       = 1               # Mono audio
CHUNK          = 1024
RECORD_SECONDS = 5               # Kitne second record karo
TEMP_WAV       = os.path.join(tempfile.gettempdir(), "ai_os_input.wav")

# ── Load Whisper model (ek baar load hota hai) ────────────────
print("  ⏳ Whisper model load ho raha hai...")
_model = whisper.load_model(WHISPER_MODEL)
print("  ✅ Whisper ready!")


def record_audio(seconds: int = RECORD_SECONDS) -> str:
    """
    Microphone se audio record karo aur temp WAV file mein save karo.
    Returns: WAV file path
    """
    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK
    )

    print(f"  🎤 Bol raha hoon... ({seconds} seconds)")
    frames = []
    for _ in range(0, int(SAMPLE_RATE / CHUNK * seconds)):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
    print("  ⏹️  Recording band!")

    stream.stop_stream()
    stream.close()
    pa.terminate()

    # WAV file save karo
    with wave.open(TEMP_WAV, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b''.join(frames))

    return TEMP_WAV


def transcribe(audio_path: str = None, language: str = None) -> dict:
    """
    Audio file ko text mein convert karo Whisper se.

    Args:
        audio_path : WAV file path (None = mic se record karo)
        language   : 'hi' Hindi, 'en' English, None = auto-detect

    Returns:
        {
          'text'    : transcribed text,
          'language': detected language,
          'success' : True/False
        }
    """
    try:
        if audio_path is None:
            audio_path = record_audio()

        # Whisper transcribe
        result = _model.transcribe(
            audio_path,
            language=language,          # None = auto-detect (Hindi/English dono)
            task="transcribe",          # 'translate' se English mein translate ho jaata
            fp16=False                  # CPU pe fp16 nahi chahiye
        )

        text      = result.get("text", "").strip()
        lang      = result.get("language", "unknown")

        return {
            "text"    : text,
            "language": lang,
            "success" : bool(text)
        }

    except Exception as e:
        return {
            "text"    : "",
            "language": "unknown",
            "success" : False,
            "error"   : str(e)
        }


def listen() -> str:
    """
    Convenience function — mic se suno aur text return karo.
    Returns: transcribed text string (empty string on failure)
    """
    result = transcribe()
    if result["success"]:
        print(f"  📝 Suna: \"{result['text']}\"  [{result['language']}]")
        return result["text"]
    else:
        print(f"  ❌ Samajh nahi aaya: {result.get('error', 'unknown error')}")
        return ""
