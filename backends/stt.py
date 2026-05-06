"""
backends/stt.py — Speech-To-Text abstraction layer

Available backends (configured via STT_BACKEND in config/kira.yaml) :
  - mlx_whisper   : Apple Silicon / Mac M-series  (current)
  - faster_whisper: Linux / ARM / Arduino Uno Q   (migration)
  - whisper_cpp   : Linux / ARM, very lightweight        (alternative migration)

To migrate to the Arduino Uno Q platform:
  1. Change STT_BACKEND=faster_whisper in config/kira.yaml
  2. Install : pip install faster-whisper
  3. No other modifications are needed in server.py
"""

import os
import io
import numpy as np
import librosa
from dotenv import load_dotenv

load_dotenv()

def _load_stt_config():
    import yaml

    path = "config/kira.yaml"

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing required config file: {path}"
        )

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if "stt" not in cfg:
        raise KeyError("Missing required section: stt")
    stt = cfg["stt"]

    return {

        "backend": stt["backend"],
        "model": stt["model"],
        "language": cfg["language"],
        "temperature": float(stt["temperature"]),
        "best_of": int(stt["best_of"]),
        "suppress_tokens": str(stt["suppress_tokens"]),
        "condition_on_previous_text": bool(
            stt["condition_on_previous_text"]
        ),
        "device": stt["device"],
        "compute_type": stt["compute_type"],
    }

_cfg = _load_stt_config()

STT_BACKEND                = _cfg["backend"]
MODEL_PATH                 = _cfg["model"]
LANGUAGE                   = _cfg["language"]
TEMPERATURE                = _cfg["temperature"]
BEST_OF                    = _cfg["best_of"]
SUPPRESS_TOKENS            = _cfg["suppress_tokens"]
CONDITION_ON_PREVIOUS_TEXT = _cfg["condition_on_previous_text"]
STT_DEVICE                 = _cfg["device"]
STT_COMPUTE_TYPE           = _cfg["compute_type"]



_backend_instance = None

def _load_backend():
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance

    if STT_BACKEND == "mlx_whisper":
        _backend_instance = _MlxWhisperBackend()
    elif STT_BACKEND == "faster_whisper":
        _backend_instance = _FasterWhisperBackend()
    elif STT_BACKEND == "whisper_cpp":
        _backend_instance = _WhisperCppBackend()
    else:
        raise ValueError(f"Unknown STT_BACKEND: '{STT_BACKEND}'. "
                         f"Valid values : mlx_whisper, faster_whisper, whisper_cpp")

    print(f"✅ STT backend loaded : {STT_BACKEND} ({MODEL_PATH})")
    return _backend_instance


# ──------------------------- Public interface (server.py only calls this) -------------------------

def transcribe(audio_data: bytes, initial_prompt: str = "") -> str | None:
    """
    Transcribes a WAV/MP3/etc. audio buffer into text.
    Returns None if the segment is detected as noise only.
    """
    audio_array = _load_audio(audio_data)
    return _load_backend().transcribe(audio_array, initial_prompt)


def backend_name() -> str:
    return STT_BACKEND


# ──------------------------- Common audio loading for all backends -------------------------

def _load_audio(audio_data: bytes) -> np.ndarray:
    with io.BytesIO(audio_data) as f:
        array, _ = librosa.load(f, sr=16000)
    return array


# ──------------------------- Backend : mlx_whisper (Apple Silicon) ─────────────────────────────────────

class _MlxWhisperBackend:
    def __init__(self):
        import mlx_whisper
        self._whisper = mlx_whisper

    def transcribe(self, audio_array: np.ndarray, initial_prompt: str) -> str | None:
        result = self._whisper.transcribe(
            audio_array,
            path_or_hf_repo=MODEL_PATH,
            initial_prompt=initial_prompt,
            temperature=TEMPERATURE,
            language=LANGUAGE,
            best_of=BEST_OF,
            suppress_tokens=SUPPRESS_TOKENS,
            condition_on_previous_text=CONDITION_ON_PREVIOUS_TEXT,
        )
        return _extract_text(result)


# ──------------------------- Backend : faster-whisper (Linux / ARM / Arduino Uno Q) -------------------------
#
# Installation  : pip install faster-whisper
# Model         : MODEL_STT=large-v3  (ou medium, small, tiny selon RAM dispo)
# Quantization  : STT_QUANTIZATION=int8  (int8 = nice compromise between speed and quality on NPU)
#
# Model download command :
#   python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8')"

class _FasterWhisperBackend:
    def __init__(self):
        from faster_whisper import WhisperModel
        self._model = WhisperModel(MODEL_PATH, device=STT_DEVICE, compute_type=STT_COMPUTE_TYPE)
        

    def transcribe(self, audio_array: np.ndarray, initial_prompt: str) -> str | None:
        segments, info = self._model.transcribe(
            audio_array,
            language=LANGUAGE,
            initial_prompt=initial_prompt or None,
            temperature=TEMPERATURE,
            best_of=BEST_OF,
            condition_on_previous_text=CONDITION_ON_PREVIOUS_TEXT,
            vad_filter=True,              # integrated noise filter
            vad_parameters={"min_silence_duration_ms": 500},
        )
        segs = list(segments)
        if not segs:
            return None

        # Filter no_speech via avg_logprob (equivalent to no_speech_prob)
        first = segs[0]
        if hasattr(first, "no_speech_prob") and first.no_speech_prob > 0.6:
            print(f"DEBUG STT : no_speech_prob={first.no_speech_prob:.2f} — ignored")
            return None

        return " ".join(s.text for s in segs).strip() or None


# ──------------------------- Backend : whisper.cpp via binding Python -------------------------
#
# Installation  : pip install pywhispercpp
# Model         : download a .bin from https://huggingface.co/ggerganov/whisper.cpp
#                 MODEL_STT=path/to/ggml-large-v3-q5_0.bin
# Advantage     : Very small memory footprint, ideal if NPU is not supported

class _WhisperCppBackend:
    def __init__(self):
        from pywhispercpp.model import Model
        self._model = Model(MODEL_PATH, language=LANGUAGE)

    def transcribe(self, audio_array: np.ndarray, initial_prompt: str) -> str | None:
        # pywhispercpp expects normalized float32
        audio_f32 = audio_array.astype(np.float32)
        segments  = self._model.transcribe(audio_f32, initial_prompt=initial_prompt or "")
        if not segments:
            return None
        text = "".join(s.text for s in segments).strip()
        return text or None


# ──------------------------- Shared utility function -------------------------

def _extract_text(result: dict) -> str | None:
    """Extracts text from an mlx_whisper result with no_speech filter."""
    segments = result.get("segments", [])
    if segments:
        no_speech_prob = segments[0].get("no_speech_prob", 0)
        print(f"DEBUG STT : no_speech_prob={no_speech_prob:.2f}")
        if no_speech_prob > 0.6:
            return None
        if len(segments) > 1:
            print(f"DEBUG STT : {len(segments)} segments, keeping the first one.")
        return segments[0].get("text", "").strip() or None
    return result.get("text", "").strip() or None
