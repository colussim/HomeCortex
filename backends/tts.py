"""
backends/tts.py — Text-To-Speech abstraction layer

Backend selection:
  kira.yaml → tts.elevenlabs_enabled: true  → ElevenLabs (requires Internet)
  kira.yaml → tts.elevenlabs_enabled: false → Piper local (default)

Available backends:
  piper        : Local neural TTS, natural French
  elevenlabs   : Cloud API, natural voice
  espeak       : Basic TTS, zero dependencies
  none         : disabled

Config from kira.yaml:
  tts:
    elevenlabs_enabled: true
    elevenlabs_voice_id: 6vTyAgAT8PncODBcLjRf
    elevenlabs_model_id: eleven_turbo_v2_5
    piper_model: fr_FR-siwis-medium
    piper_models_dir: /usr/local/whisper-server/models/piper
    piper_speed: 1.0
    cache_enabled: true
    cache_db: data/tts_cache.db

Secrets from .env only:
  ELEVENLABS_API_KEY=sk_...

Output format: WAV PCM mono 16-bit, ready to play via I2S on ESP32.
"""

import os
import io
import wave
import sqlite3
import hashlib
import subprocess
from dotenv import load_dotenv

load_dotenv()


# ──------------------------- Load config from kira.yaml -------------------------

def _load_tts_config() -> dict:
    """Load TTS config from config/kira.yaml. Fallback to default values."""
    import yaml

    path = "config/kira.yaml"

    if not os.path.exists(path):

        raise FileNotFoundError(f"Missing required config file: {path}")

    with open(path, encoding="utf-8") as f:

        cfg = yaml.safe_load(f) or {}

    if "tts" not in cfg:

        raise KeyError("Missing required section: tts")

    return cfg["tts"]


_cfg = _load_tts_config()

TTS_MODEL      = _cfg["piper_model"]
TTS_MODELS_DIR = _cfg["piper_models_dir"]
TTS_SPEED      = float(_cfg["piper_speed"])

# ElevenLabs — API key from .env (secret), rest from kira.yaml
_el_enabled   = _cfg.get("elevenlabs_enabled", False)
EL_API_KEY    = os.getenv("ELEVENLABS_API_KEY", "")
EL_VOICE_ID = _cfg["elevenlabs_voice_id"]
EL_MODEL_ID = _cfg["elevenlabs_model_id"]

# XTTS : local voice cloning (Coqui TTS)
XTTS_SPEAKER_WAV = _cfg["xtts_speaker_wav"]
XTTS_LANGUAGE    = _cfg["xtts_language"]

# Backend actif : ElevenLabs if activated AND key present, otherwise Piper
TTS_BACKEND = "elevenlabs" if (_el_enabled and EL_API_KEY) else "piper"

# Cache TTS
TTS_CACHE_ENABLED = _cfg["cache_enabled"]
TTS_CACHE_DB      = _cfg["cache_db"]
        
_tts_cache: "TTSCache | None" = None


# ──------------------------- Cache TTS -------------------------

class TTSCache:
    """
    SQLite cache for frequent TTS responses.
    MD5 hash of the text → WAV bytes.
    Avoids calling ElevenLabs for the same phrases.
    Gain: 0ms instead of ~400ms for cached phrases.
    """
    def __init__(self):
        os.makedirs(os.path.dirname(TTS_CACHE_DB) or ".", exist_ok=True)
        self._conn = sqlite3.connect(TTS_CACHE_DB, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tts_cache (
                hash     TEXT PRIMARY KEY,
                text     TEXT NOT NULL,
                wav      BLOB NOT NULL,
                backend  TEXT NOT NULL,
                hits     INTEGER DEFAULT 1,
                created  TEXT DEFAULT (datetime('now')),
                last_hit TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()
        count = self._conn.execute("SELECT COUNT(*) FROM tts_cache").fetchone()[0]
        print(f"✅ TTS Cache: {count} entries in database")

    def get(self, text: str, backend: str) -> bytes | None:
        key = hashlib.md5(f"{backend}:{text}".encode()).hexdigest()
        row = self._conn.execute(
            "SELECT wav FROM tts_cache WHERE hash=? AND backend=?", (key, backend)
        ).fetchone()
        if row:
            self._conn.execute(
                "UPDATE tts_cache SET hits=hits+1, last_hit=datetime('now') WHERE hash=?",
                (key,)
            )
            self._conn.commit()
            return bytes(row[0])
        return None

    def set(self, text: str, backend: str, wav: bytes):
        key = hashlib.md5(f"{backend}:{text}".encode()).hexdigest()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO tts_cache (hash, text, wav, backend) VALUES (?,?,?,?)",
                (key, text[:200], wav, backend)
            )
            self._conn.commit()
        except Exception as e:
            print(f"⚠️  TTS Cache write error: {e}")

    def stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT COUNT(*), SUM(hits), SUM(LENGTH(wav)) FROM tts_cache"
        ).fetchone()
        return {
            "entries":    rows[0] or 0,
            "total_hits": rows[1] or 0,
            "size_bytes": rows[2] or 0,
        }

    def clear(self):
        self._conn.execute("DELETE FROM tts_cache")
        self._conn.commit()


def _get_cache() -> "TTSCache | None":
    global _tts_cache
    if not TTS_CACHE_ENABLED:
        return None
    if _tts_cache is None:
        try:
            _tts_cache = TTSCache()
        except Exception as e:
            print(f"⚠️  TTS Cache init failed: {e}")
    return _tts_cache


# ── Backend loader ─────────────────────────────────────────────────────────────

_backend_instance = None

def _load_backend():
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance
    if TTS_BACKEND == "elevenlabs":
        _backend_instance = _ElevenLabsBackend()
    elif TTS_BACKEND == "xtts":
        _backend_instance = _XTTSBackend()
    elif TTS_BACKEND == "piper":
        _backend_instance = _PiperBackend()
    elif TTS_BACKEND == "espeak":
        _backend_instance = _ESpeakBackend()
    elif TTS_BACKEND == "none":
        _backend_instance = _NoneBackend()
    else:
        raise ValueError(f"TTS_BACKEND inconnu : '{TTS_BACKEND}'")
    print(f"✅ TTS backend chargé : {TTS_BACKEND}")
    return _backend_instance


# ── Interface publique ─────────────────────────────────────────────────────────

def synthesize(text: str) -> bytes | None:
    """
    Synthesizes text into WAV 16kHz mono 16-bit.
    Checks the cache first — calls the backend only if absent.
    Returns None if backend=none.
    """
    if not text or not text.strip():
        return None

    clean = _clean_for_voice(text)
    cache = _get_cache()

    # Cache hit — 0ms instead of ~400ms ElevenLabs
    if cache:
        cached = cache.get(clean, TTS_BACKEND)
        if cached:
            print(f"  TTS cache hit ({TTS_BACKEND}) : {clean[:40]}")
            return cached

    # Cache miss — generate via the backend
    wav = _load_backend().synthesize(clean)

    # Save to cache if successful
    if cache and wav and len(wav) > 44:
        cache.set(clean, TTS_BACKEND, wav)

    return wav


def cache_stats() -> dict:
    """TTS cache statistics."""
    cache = _get_cache()
    return cache.stats() if cache else {"entries": 0, "total_hits": 0, "size_bytes": 0}


def cache_clear():
    """Clears the TTS cache — called by DELETE /tts/cache."""
    cache = _get_cache()
    if cache:
        cache.clear()
        print("✅ TTS Cache cleared")


def backend_name() -> str:
    return TTS_BACKEND


def is_enabled() -> bool:
    return TTS_BACKEND != "none"


def _clean_for_voice(text: str) -> str:
    import re
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#{1,6}\s', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'`+', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ── Backends ──────────────────────────────────────────────────────────────────

class _ElevenLabsBackend:
    """TTS via API ElevenLabs. Nécessite ELEVENLABS_API_KEY dans .env."""

    def __init__(self):
        if not EL_API_KEY:
            raise ValueError(
                "ELEVENLABS_API_KEY missing in .env\n"
                "Add: ELEVENLABS_API_KEY=sk_..."
            )
        print(f"✅ ElevenLabs voice: {EL_VOICE_ID} | model: {EL_MODEL_ID}")

    def synthesize(self, text: str) -> bytes:
        import requests as req

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}"
        headers = {
            "xi-api-key":   EL_API_KEY,
            "Content-Type": "application/json",
            "Accept":       "audio/mpeg",
        }
        payload = {
            "text":     text,
            "model_id": EL_MODEL_ID,
            "voice_settings": {
                "stability":        0.5,
                "similarity_boost": 0.75,
            },
        }

        r = req.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")

        mp3_bytes = r.content
        if not mp3_bytes:
            raise RuntimeError("ElevenLabs returned 0 bytes")

        wav_bytes = _mp3_to_wav(mp3_bytes)
        duration  = (len(wav_bytes) - 44) / (16000 * 2)
        print(f"  ElevenLabs: {len(mp3_bytes)} bytes MP3 → {len(wav_bytes)} bytes WAV = {duration:.2f}s")
        return wav_bytes


def _mp3_to_wav(mp3_bytes: bytes, target_rate: int = 16000) -> bytes:
    """Converts MP3 bytes to 16kHz mono 16-bit WAV via ffmpeg."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_in:
        f_in.write(mp3_bytes)
        mp3_path = f_in.name
    wav_path = mp3_path.replace(".mp3", ".wav")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", mp3_path,
            "-ar", str(target_rate),
            "-ac", "1",
            "-sample_fmt", "s16",
            wav_path
        ], check=True, capture_output=True)
        with open(wav_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(mp3_path)
        if os.path.exists(wav_path):
            os.unlink(wav_path)


class _XTTSBackend:
    """Local TTS with voice cloning via Coqui XTTS v2."""

    def __init__(self):
        if not os.path.exists(XTTS_SPEAKER_WAV):
            raise FileNotFoundError(
                f"XTTS speaker sample not found: {XTTS_SPEAKER_WAV}\n"
                f"Run first: python3 extract_speaker_sample.py"
            )
        try:
            from TTS.api import TTS as CoquiTTS
        except ImportError:
            raise ImportError("Coqui TTS not installed — run: pip install TTS")

        print(f"⏳ Loading XTTS v2 (first launch ~30s)...")
        self._tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2")
        print(f"✅ XTTS v2 ready — speaker: {XTTS_SPEAKER_WAV}")

    def synthesize(self, text: str) -> bytes:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            self._tts.tts_to_file(
                text=text,
                speaker_wav=XTTS_SPEAKER_WAV,
                language=XTTS_LANGUAGE,
                file_path=tmp_path,
                speed=1.0,
            )
            with open(tmp_path, "rb") as f:
                wav_bytes = f.read()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if len(wav_bytes) <= 44:
            raise RuntimeError(f"XTTS WAV empty: {len(wav_bytes)} bytes")

        wav_bytes = _resample_wav_to_16k(wav_bytes)
        duration = (len(wav_bytes) - 44) / (16000 * 2)
        print(f"  XTTS: {len(wav_bytes)} bytes WAV = {duration:.2f}s @ 16kHz")
        return wav_bytes


def _resample_wav_to_16k(wav_bytes: bytes) -> bytes:
    """Resample a WAV to 16kHz mono via ffmpeg."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix="_in.wav", delete=False) as f_in:
        f_in.write(wav_bytes)
        in_path = f_in.name
    out_path = in_path.replace("_in.wav", "_out.wav")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", in_path,
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            out_path,
        ], check=True, capture_output=True)
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        for p in [in_path, out_path]:
            if os.path.exists(p):
                os.unlink(p)


class _PiperBackend:
    """TTS neural local via Piper."""

    def __init__(self):
        try:
            from piper import PiperVoice
            model_path = os.path.join(TTS_MODELS_DIR, f"{TTS_MODEL}.onnx")
            if not os.path.exists(model_path):
                raise FileNotFoundError(
                    f"Piper model not found: {model_path}\n"
                    f"Download: wget -P {TTS_MODELS_DIR} "
                    f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
                    f"fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx"
                )
            self._voice = PiperVoice.load(model_path)
        except ImportError:
            raise ImportError("pip install piper-tts")

    def synthesize(self, text: str) -> bytes:
        buf      = io.BytesIO()
        wav_file = wave.open(buf, "wb")
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(self._voice.config.sample_rate)
        self._voice.synthesize_wav(text, wav_file)
        wav_file.close()

        wav_bytes = buf.getvalue()
        if len(wav_bytes) <= 44:
            raise RuntimeError(f"Piper WAV empty: {len(wav_bytes)} bytes")

        sample_rate = self._voice.config.sample_rate
        duration    = (len(wav_bytes) - 44) / (sample_rate * 2)
        print(f"  Piper: {len(wav_bytes)} bytes WAV = {duration:.2f}s @ {sample_rate}Hz")
        return wav_bytes


class _ESpeakBackend:
    """Basic TTS via espeak — zero dependencies."""

    def __init__(self):
        r = subprocess.run(["which", "espeak-ng"], capture_output=True)
        self._cmd = "espeak-ng" if r.returncode == 0 else "espeak"

    def synthesize(self, text: str) -> bytes:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                [self._cmd, "-v", "fr", "-s", str(int(130 * TTS_SPEED)),
                 "-w", tmp_path, text],
                check=True, capture_output=True
            )
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            os.unlink(tmp_path)


class _NoneBackend:
    """TTS disabled — satellite uses its fixed audio files."""

    def __init__(self):
        print("ℹ️  TTS disabled — satellite uses its fixed audio files")

    def synthesize(self, text: str) -> None:
        return None
