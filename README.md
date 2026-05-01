[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024+-orange.svg)](https://home-assistant.io)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Language](https://img.shields.io/badge/Language-French%20%7C%20Adaptable-purple.svg)](#language)

# 🎙️ HomeCortex — Local Voice AI for Smart Home

> **A fully local, privacy-first voice assistant backend for Home Assistant.**  
> No cloud. No subscriptions. No data leaving your network.



---

## ✨ What is HomeCortex?

HomeCortex is an open-source, self-hosted AI backend for smart homes. 
It connects ESP32-S3 voice satellites to your Home Assistant instance, processes natural language locally using LLMs, and generates natural voice responses all without sending a single byte to the cloud.

Kira is the default voice assistant powered by HomeCortex.

```
You say: "Allume, Lampe Salon"      → Lamp turns on instantly
You say: "Quelle est la météo ?"    → Real weather data, spoken aloud
You say: "Éteins chambre 1"         → All bedroom lights turn off
You say: "Quelle heure est-il ?"    → Instant answer, no LLM needed
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         YOUR HOME NETWORK                           │
│                                                                     │
│  ┌──────────────┐    WAV audio     ┌──────────────────────────────┐ │
│  │ ESP32-S3     │ ───────────────► │  Kira Backend (FastAPI)      │ │
│  │ Satellite    │                  │                              │ │
│  │              │ ◄─────────────── │  1. Whisper MLX  (STT)       │ │
│  │ • WakeNet    │    JSON + WAV    │  2. Ollama LLM   (routing)   │ │
│  │ • VAD / AEC  │                  │  3. Home Assistant (actions) │ │
│  │ • I2S audio  │                  │  4. ElevenLabs / Piper (TTS) │ │
│  └──────────────┘                  └──────────────────────────────┘ │
│                                              │                      │
│                                    ┌─────────▼──────────┐          │
│                                    │  Home Assistant    │          │
│                                    │  /api/services     │          │
│                                    │  /api/states       │          │
│                                    └────────────────────┘          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Why Mac M-series First?

Kira is designed with a **two-phase hardware strategy**:

### Phase 1 — Development on Apple Silicon (now)

The Apple Mac Studio / Mac Mini M-series is an ideal development platform:

| Capability | Why it matters |
|---|---|
| **Neural Engine (16+ TOPS)** | Runs Whisper MLX at native speed — STT in ~0.5s |
| **Unified memory (32 GB)** | LLM + Whisper + server in RAM simultaneously |
| **Metal GPU** | Ollama accelerated inference out of the box |
| **Low power (~15W idle)** | Always-on home server without electricity concerns |
| **macOS stability** | Production-ready for 24/7 operation |

### Phase 2 — Migration to Arduino VENTUNO Q (coming soon)

```
┌─────────────────────────────────────────────────────────┐
│            MIGRATION PATH: M1 Pro → VENTUNO Q           │
│                                                         │
│  Mac M1 Pro          Arduino VENTUNO Q                  │
│  ─────────────       ─────────────────────              │
│  STT: mlx_whisper →  STT: faster_whisper                │
│  LLM: ollama      →  LLM: llama_cpp (GGUF)              │
│  TTS: ElevenLabs  →  TTS: Coqui XTTS v2 (local clone)  │
│                                                         │
│  Just 2 lines in .env — no code changes needed          │
│                                                         │
│  Qualcomm Dragonwing IQ8 — 40 TOPS NPU                 │
│  16 GB LPDDR5 — runs Qwen2.5-3B at ~800ms/query        │
└─────────────────────────────────────────────────────────┘
```

The entire backend is designed for **zero-code migration** — swap hardware by changing two environment variables.

---

## 🌍 Language Support

Kira is **primarily designed for French** and optimized for French Home Assistant entity names, aliases, and voice patterns. However, the architecture is fully language-agnostic:

- Whisper supports 99 languages — change `LANGUAGE=fr` to any ISO code
- Ollama runs any multilingual model (Qwen2.5, Mistral, Llama 3.2)
- `prompt.txt` is the only file that needs translation
- HA entity aliases work in any language

**To adapt Kira to English or another language:**
1. Change `LANGUAGE=en` in `.env`
2. Translate `prompt.txt` to your language
3. Update `WHISPER_HINT` keywords
4. Replace ElevenLabs voice ID with your preferred voice

---

## ⚡ Performance

Real-world latency on Mac M1 Pro (Apple Silicon, Wi-Fi satellite):

```
Request type              Latency    Path
─────────────────────     ───────    ─────────────────────────────
"Quelle heure est-il ?"   ~0.7s     Whisper → bypass (no LLM)
"Allume lampe salon"      ~0.8s     Whisper → bypass HA direct
"Quelle est la météo ?"   ~1.0s     Whisper → bypass → Open-Meteo (cached)
"Température escalier ?"  ~0.6s     Whisper → bypass → HA sensor
"Qui est Elon Musk ?"     ~3.5s     Whisper → LLM → web_search
General conversation      ~3.0s     Whisper → LLM → ElevenLabs TTS
```

**Optimization layers:**
- 🔀 **Smart bypass routing** — HA commands, time, weather skip the LLM entirely
- 💾 **TTS cache** — repeated phrases served in 0ms (SQLite)
- 🌤️ **Weather cache** — 15-minute cache avoids redundant API calls
- 🔥 **Ollama keep_alive** — model stays hot in memory, no cold start
- ⚡ **ElevenLabs Turbo** — `eleven_turbo_v2_5` at ~220ms vs 970ms for multilingual

---

## 📦 Project Structure

```
kira-voice/
├── server.py                     # FastAPI main — full pipeline
├── prompt.txt                    # Kira's personality (loaded at startup)
│
├── backends/
│   ├── stt.py                    # STT abstraction (Whisper MLX / faster-whisper)
│   ├── llm.py                    # LLM abstraction (Ollama / llama.cpp / OpenAI)
│   ├── tts.py                    # TTS abstraction (ElevenLabs / Piper / XTTS)
│   ├── memory.py                 # Long-term SQLite memory + query stats
│   └── speaker.py                # Speaker identification (pyannote-audio)
│
├── services/
│   ├── get_weather.py            # Open-Meteo weather (free, no API key)
│   ├── get_ha_state.py           # Read HA entity states
│   ├── web_search.py             # DuckDuckGo search (no API key)
│   ├── ha_entities_loader.py     # Dynamic HA entity + alias loading
│   └── proactive.py              # Scheduled announcements (APScheduler)
│
├── config/
│   ├── satellites.json           # Satellite tokens → room mapping
│   ├── tools_config.json         # LLM tool definitions
│   ├── ha_entities.json          # HA entity cache (auto-generated)
│   ├── kira_memory.db            # SQLite: facts, history, query stats
│   └── tts_cache.db              # SQLite: TTS audio cache
│
├── HA/
│   └── core.entity_registry      # Copied from HA — provides aliases
│
├── models/
│   ├── piper/                    # Piper TTS voice models
│   └── xtts/                     # Coqui XTTS speaker reference WAV
│
└── esp32/
    ├── kira_client.h             # ESP32-S3 full pipeline client
    ├── kira_play_endpoint.h      # ESP32 HTTP /play endpoint (proactive TTS)
    └── config.h                  # Satellite configuration
```

---

## 🔄 Request Pipeline

```
                    ┌─────────────────────────────────────────┐
   ESP32 POST       │           process_kira()                │
   /transcribe ───► │                                         │
   (WAV bytes)      │  1. authenticate_satellite()            │
                    │     X-Token → {room, id, location}      │
                    │                                         │
                    │  2. transcribe_audio()                  │
                    │     Whisper MLX → text                  │
                    │     (WHISPER_HINT = HA aliases)         │
                    │                                         │
                    │  3. Smart bypass routing                │
                    │     ├─ Time/date?    → instant reply    │
                    │     ├─ HA sensor?   → get_ha_state()   │
                    │     ├─ HA command?  → execute_category()│
                    │     └─ Weather?     → get_weather()     │
                    │                                         │
                    │  4. router_llm() [if no bypass]         │
                    │     ├─ tool_calls  → services/          │
                    │     └─ text reply  → SPEECH             │
                    │                                         │
                    │  5. Return JSON                         │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │  {                                      │
                    │    "status":   "success",               │
                    │    "heard":    "Allume lampe salon",    │
                    │    "reply":    "Lampe salon allumée.",  │
                    │    "category": "HA",                    │
                    │    "ha_ack":   "ok",                    │
                    │    "expect_reply": false                │
                    │  }                                      │
                    └──────────────┬──────────────────────────┘
                                   │
         ┌─────────────────────────▼──────────────────────────┐
         │  ESP32 decision                                     │
         │  category == "HA"     → play fixed audio file      │
         │  category == "SPEECH" → POST /tts → WAV → I2S     │
         └─────────────────────────────────────────────────────┘
```

---

## 🛠️ Installation

### Requirements

- macOS with Apple Silicon (M1/M2/M3/M4) or Linux x86_64
- Python 3.11+ (3.14 not supported for XTTS)
- [Ollama](https://ollama.ai) installed
- Home Assistant instance on local network
- ESP32-S3 satellite with ESP-SR framework

### Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/kira-voice.git
cd kira-voice

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your HA URL, token, ElevenLabs key, etc.

# Copy HA entity registry (for voice aliases)
mkdir -p HA
cp /path/to/homeassistant/.storage/core.entity_registry HA/

# Download LLM
ollama pull qwen2.5:3b

# Start
python server.py
```

### Environment Variables

```bash
# ── Server ────────────────────────────────────
KIRA_API_TOKEN=your_admin_token
USE_LLM=1

# ── STT (Speech-to-Text) ──────────────────────
STT_BACKEND=mlx_whisper
MODEL_STT=mlx-community/whisper-large-v3-turbo
LANGUAGE=fr

# ── LLM ───────────────────────────────────────
LLM_BACKEND=ollama
LLM_MODEL=qwen2.5:3b
LLM_TEMPERATURE=0.7
LLM_NUM_PREDICT=50

# ── TTS (Text-to-Speech) ──────────────────────
ENABLE_ELEVENLABS=1                         # 1=ElevenLabs, 0=Piper
ELEVENLABS_API_KEY=sk_...
ELEVENLABS_VOICE_ID=your_voice_id
ELEVENLABS_MODEL_ID=eleven_turbo_v2_5       # fast + quality
TTS_CACHE_ENABLED=1

# ── Home Assistant ─────────────────────────────
HA_URL=http://192.168.1.x:8123/api          # with /api for conversation
HA_URL_C=http://192.168.1.x:8123            # without /api for states
HA_TOKEN=your_long_lived_access_token

# ── Memory ─────────────────────────────────────
MEMORY_DB=config/kira_memory.db

# ── Proactive announcements ────────────────────
PROACTIVE_ENABLED=1
PROACTIVE_TZ=Europe/Paris
```

---

## 🔌 API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/transcribe` | Satellite token | Main pipeline — WAV in, JSON out |
| `POST` | `/tts` | Satellite token | Text → WAV binary (16kHz mono) |
| `POST` | `/alert` | Admin token | Proactive announcement → satellite |
| `GET` | `/debug-auth` | Satellite token | Token diagnostic without audio |
| `GET` | `/memory/stats` | Admin token | Most frequent queries |
| `GET` | `/memory/facts` | Admin token | Memorized facts |
| `DELETE` | `/tts/cache` | Admin token | Clear TTS cache |
| `DELETE` | `/memory/{id}` | Admin token | Clear satellite history |
| `GET` | `/health` | None | Server status + loaded models |

---

## 🧠 Intelligence Layers

### Smart Bypass Routing
```
Query type                  Bypass          Latency
──────────────────────────  ──────────────  ───────
"quelle heure est-il ?"    datetime.now()  ~0ms
"allume [known entity]"     HA direct       ~0ms
"quelle est la météo ?"    Open-Meteo      ~50ms (cached)
"température escalier ?"   HA sensor       ~100ms
Everything else             LLM (Ollama)    ~2000ms
```

### Long-term Memory (SQLite)
- **Facts** — "Souviens-toi que j'aime la lumière tamisée" → stored forever
- **History** — last N exchanges per satellite, survives restarts
- **Query stats** — tracks frequent questions, adapts Kira's behavior
- **Adaptive context** — auto-detects patterns ("Genève = default city")

### HA Alias Integration
Kira reads `core.entity_registry` directly from Home Assistant to load all voice aliases defined in Assist:
```
"chambre 1", "chambre un" → light.group_chambre1
"petit salon"              → switch.lampe_petit_salon
"escalier"                 → switch.lampe_escalier
```
No manual configuration needed — define aliases once in HA, Kira picks them up automatically.

---

## 🗺️ Roadmap

```
✅ Done                          🔄 In Progress        📋 Planned
──────────────────────────────   ─────────────────     ──────────────────────
✅ Multi-satellite auth          🔄 Speaker ID         📋 VENTUNO Q migration
✅ Whisper MLX STT               🔄 Coqui XTTS voice   📋 Vision (LLaVA)
✅ Ollama LLM routing                                  📋 Multi-satellite sync
✅ HA conversation API                                 📋 HA webhook alerts
✅ ElevenLabs + Piper TTS                              📋 Timer/reminder tool
✅ Smart bypass routing
✅ Long-term SQLite memory
✅ HA alias auto-loading
✅ Room-aware entity resolution
✅ Proactive announcements
✅ TTS cache
✅ Weather cache
✅ DuckDuckGo web search
✅ HA state queries
```

---

## 🔧 Hardware

### Current: Apple Mac M1 Pro
- 10-core CPU, 32 GB unified memory
- Whisper runs on Neural Engine (~0.5s per utterance)
- Ollama runs on GPU (qwen2.5:3b ~1.5s)
- Always-on at ~15W

### Satellites: ESP32-S3
- ESP-SR framework (WakeNet + AFE + VADNet)
- 16kHz mono audio capture
- Direct WAV POST to backend over Wi-Fi
- WAV playback via I2S speaker

### Next: Arduino VENTUNO Q
```
Qualcomm Dragonwing IQ8
├── 40 TOPS NPU → Whisper + LLM inference
├── 16 GB LPDDR5 → Qwen2.5-3B + Coqui XTTS
└── Gigabit Ethernet → <10ms network latency

Migration: change 2 lines in .env
STT_BACKEND=faster_whisper
LLM_BACKEND=llama_cpp
```

---

## 📜 License

MIT License — see [LICENSE](LICENSE)

---

## 🙏 Acknowledgments

- [OpenAI Whisper](https://github.com/openai/whisper) — speech recognition
- [ml-explore/mlx-examples](https://github.com/ml-explore/mlx-examples) — Whisper MLX
- [Ollama](https://ollama.ai) — local LLM inference
- [Home Assistant](https://home-assistant.io) — smart home platform
- [ElevenLabs](https://elevenlabs.io) — neural TTS
- [Coqui TTS](https://github.com/coqui-ai/TTS) — open-source TTS + voice cloning
- [Open-Meteo](https://open-meteo.com) — free weather API
- [ESP-SR](https://github.com/espressif/esp-sr) — ESP32 speech recognition
