"""
backends/speaker.py — Speaker recognition with Pyannote Audio

Identifies who is speaking among the registered people in the house.
The information is injected into the LLM system prompt to personalize
responses ("Hello Emmanuel" vs "Hello Véronique").

Architecture :
  - pyannote/wespeaker-voxceleb-resnet34-LM : lightweight voice embedding model
  - 512-d embeddings stored in SQLite (table speaker_profiles)
  - Cosine similarity for identification
  - Configurable confidence threshold (SPEAKER_THRESHOLD)

Installation :
  pip install pyannote.audio torch torchaudio

First launch (model download ~400 MB) :
  python3 -c "from backends.speaker import init; init()"

Enrollment (one-time per person) :
  Record 30 seconds of your voice, then:
  POST /enroll  { "name": "Emmanuel", "audio": <wav bytes base64> }

Variables .env :
  SPEAKER_ENABLED=1
  SPEAKER_THRESHOLD=0.75     # 0.0-1.0, higher = stricter
  SPEAKER_MODEL=pyannote/wespeaker-voxceleb-resnet34-LM
  HF_TOKEN=hf_...            # HuggingFace token (pyannote model requires license acceptance)
"""

import os
import io
import struct
import sqlite3
import numpy as np
from dotenv import load_dotenv

load_dotenv()

def _load_speaker_config() -> dict:
    import yaml

    path = "config/kira.yaml"

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing required config file: {path}"
        )

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if "speaker" not in cfg:
        raise KeyError("Missing required section: speaker")

    speaker = cfg["speaker"]

    return {
        "enabled": bool(speaker["enabled"]),
        "threshold": float(speaker["threshold"]),
        "model": speaker["model"],
        "memory_db": speaker["memory_db"],
    }


_cfg = _load_speaker_config()

SPEAKER_ENABLED   = _cfg["enabled"]
SPEAKER_THRESHOLD = _cfg["threshold"]
SPEAKER_MODEL     = _cfg["model"]
MEMORY_DB         = _cfg["memory_db"]

# Secret only from .env
HF_TOKEN = os.getenv("HF_TOKEN")

if SPEAKER_ENABLED and not HF_TOKEN:
    raise RuntimeError(
        "HF_TOKEN is required when speaker identification is enabled"
    )

_model     = None
_conn: sqlite3.Connection | None = None
_profiles: dict[str, np.ndarray] = {}   # cache RAM : name → embedding


# ──------------------------- Initialization -------------------------

def init():
    """
    Load the pyannote model and profiles from SQLite.
    Called once at the startup of server.py if SPEAKER_ENABLED=1.
    """
    global _model, _conn

    if not SPEAKER_ENABLED:
        print("ℹ️  Speaker recognition disabled (SPEAKER_ENABLED=0)")
        return

    # ──------------------------- Database -------------------------
    os.makedirs(os.path.dirname(MEMORY_DB) or ".", exist_ok=True)
    _conn = sqlite3.connect(MEMORY_DB, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS speaker_profiles (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT    NOT NULL UNIQUE,
            embedding BLOB    NOT NULL,           -- numpy float32 array serialized
            n_samples INTEGER DEFAULT 1,           -- number of merged enrollments
            created   TEXT    DEFAULT (datetime('now')),
            updated   TEXT    DEFAULT (datetime('now'))
        )
    """)
    _conn.commit()

    # ──------------------------- Load the pyannote model -------------------------
    try:
        from pyannote.audio import Model
        from pyannote.audio import Inference

        print(f"⏳ Loading speaker ID model : {SPEAKER_MODEL}")
        kwargs = {}
        if HF_TOKEN:
            kwargs["use_auth_token"] = HF_TOKEN

        raw_model = Model.from_pretrained(SPEAKER_MODEL, **kwargs)
        _model    = Inference(raw_model, window="whole")
        print(f"✅ Speaker ID ready : {SPEAKER_MODEL}")
    except ImportError:
        print("⚠️  pyannote.audio not installed — pip install pyannote.audio")
        print("   Speaker recognition disabled.")
        return
    except Exception as e:
        print(f"⚠️  Unable to load {SPEAKER_MODEL} : {e}")
        if "HF_TOKEN" in str(e) or "token" in str(e).lower():
            print(f"   → Accept the license at https://hf.co/{SPEAKER_MODEL}")
            print("   → Then add HF_TOKEN=hf_... in .env")
        return

    # ──------------------------- Load profiles from SQLite -------------------------
    _load_profiles_from_db()
    print(f"✅ {len(_profiles)} voice profile(s) loaded : {list(_profiles.keys())}")


def _load_profiles_from_db():
    """Load all embeddings from SQLite into the RAM cache."""
    global _profiles
    if not _conn:
        return
    rows = _conn.execute("SELECT name, embedding FROM speaker_profiles").fetchall()
    _profiles = {}
    for row in rows:
        emb = np.frombuffer(row["embedding"], dtype=np.float32).copy()
        _profiles[row["name"]] = emb


# ──------------------------- Identification -------------------------

def identify(wav_bytes: bytes) -> tuple[str | None, float]:
    """
    Identify the speaker in a 16kHz mono 16-bit WAV buffer.

    Returns:
        (name, confidence) if identified above the threshold
        (None, score)      if unknown or below the threshold
    """
    if not SPEAKER_ENABLED or _model is None or not _profiles:
        return None, 0.0

    try:
        embedding = _compute_embedding(wav_bytes)
        if embedding is None:
            return None, 0.0

        # Cosine comparison with all profiles
        best_name  = None
        best_score = -1.0

        for name, ref_emb in _profiles.items():
            score = _cosine_similarity(embedding, ref_emb)
            if score > best_score:
                best_score = score
                best_name  = name

        if best_score >= SPEAKER_THRESHOLD:
            print(f"  Speaker ID : {best_name} ({best_score:.3f})")
            return best_name, float(best_score)
        else:
            print(f"  Speaker ID : unknown (best={best_name}, score={best_score:.3f} < {SPEAKER_THRESHOLD})")
            return None, float(best_score)

    except Exception as e:
        print(f"  Speaker ID error : {e}")
        return None, 0.0


def _compute_embedding(wav_bytes: bytes) -> np.ndarray | None:
    """Compute the vocal embedding of a WAV buffer using pyannote."""
    if _model is None:
        return None

    try:
        import torchaudio
        import torch

        # Decode the WAV from bytes
        buf = io.BytesIO(wav_bytes)
        waveform, sample_rate = torchaudio.load(buf)

        # Resample if necessary (pyannote expects 16kHz)
        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000)
            waveform  = resampler(waveform)

        # pyannote expects a dict {"waveform": tensor, "sample_rate": int}
        audio_input = {"waveform": waveform, "sample_rate": 16000}
        embedding   = _model(audio_input)

        return np.array(embedding).flatten().astype(np.float32)

    except Exception as e:
        print(f"  Embedding error : {e}")
        return None


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors. Returns 0.0-1.0."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ──------------------------- Enrollment -------------------------

def enroll(name: str, wav_bytes: bytes) -> dict:
    """
    Enroll or update a person's voice profile.

    The embedding is averaged with previous enrollments if the profile already exists.
    Recommended: 30 seconds of clean audio, natural voice, in real conditions.

    Args:
        name      : person's first name ("Emmanuel", "Véronique")
        wav_bytes : 16kHz mono WAV audio

    Returns:
        dict with status, name, n_samples
    """
    if not SPEAKER_ENABLED or _model is None:
        return {"status": "error", "detail": "Speaker ID not initialized"}

    embedding = _compute_embedding(wav_bytes)
    if embedding is None:
        return {"status": "error", "detail": "Unable to compute embedding"}

    if not _conn:
        return {"status": "error", "detail": "Database not initialized"}

    existing = _conn.execute(
        "SELECT id, embedding, n_samples FROM speaker_profiles WHERE name=?", (name,)
    ).fetchone()

    if existing:
        # Average with existing embedding (incremental learning)
        old_emb  = np.frombuffer(existing["embedding"], dtype=np.float32).copy()
        n        = existing["n_samples"]
        new_emb  = (old_emb * n + embedding) / (n + 1)
        new_emb  = new_emb.astype(np.float32)

        _conn.execute(
            "UPDATE speaker_profiles SET embedding=?, n_samples=?, updated=datetime('now') WHERE name=?",
            (new_emb.tobytes(), n + 1, name)
        )
        n_samples = n + 1
        action    = "updated"
    else:
        _conn.execute(
            "INSERT INTO speaker_profiles (name, embedding) VALUES (?, ?)",
            (name, embedding.tobytes())
        )
        n_samples = 1
        action    = "created"

    _conn.commit()

    # Update the RAM cache
    _profiles[name] = new_emb if existing else embedding
    print(f"✅ Voice profile {action} : {name} ({n_samples} enrollment(s))")

    return {"status": "ok", "name": name, "n_samples": n_samples, "action": action}


def list_profiles() -> list[dict]:
    """Returns the list of enrolled voice profiles."""
    if not _conn:
        return []
    rows = _conn.execute(
        "SELECT name, n_samples, created, updated FROM speaker_profiles ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_profile(name: str) -> bool:
    """Deletes a voice profile."""
    if not _conn:
        return False
    _conn.execute("DELETE FROM speaker_profiles WHERE name=?", (name,))
    _conn.commit()
    _profiles.pop(name, None)
    return True
