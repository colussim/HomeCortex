"""
Kira Voice Assistant — Main FastAPI server.
Handles audio transcription, LLM routing, Home Assistant control and TTS synthesis.
"""

import os
import uvicorn
import json
import requests
import importlib
from collections import deque
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from backends import tts as tts_backend
from dotenv import load_dotenv
from datetime import datetime

from backends import stt, llm, memory

# ------------------------- Centralized YAML configuration ------------------------- 
# Loaded before all everything else — provides KIRA, LANG, PERSONAS, PHONETIC, ROOM_GROUPS
load_dotenv()  # Load the .env file first for the secrets.
try:
    from services.config_loader import KIRA, LANG, PERSONAS, PHONETIC, ROOM_GROUPS
    print("✅ Config YAML chargée")
except ImportError as _ce:
    raise RuntimeError(
        "❌ config_loader.py is required but could not be imported. "
        "❌ Check that the file exists and that all dependencies are installed."
    ) from _ce

# ------------------------- Speaker identification -------------------------
#  Enable/Disable from kira.yaml → speaker.enabled
try:
    from backends import speaker as speaker_backend
    if KIRA.speaker_enabled:
        speaker_backend.init()
        print("✅ Speaker ID initialized")
    else:
        speaker_backend = None
        print("ℹ️  Speaker ID disabled (kira.yaml → speaker.enabled: false)")
except Exception as _e:
    speaker_backend = None
    print(f"⚠️  Speaker ID not available: {_e}")

# ──────────────────────────────────────────────
# CONFIG LOADING AT STARTUP
# ──────────────────────────────────────────────

def load_prompt_from_file(file_name="prompt.txt"):
    """
    Load the system prompt for the LLM (kept complete).
    WARNING: do NOT use this full text as the initial_prompt for Whisper.
    Whisper hallucinates the prompt if the audio is short or ambiguous.
    """
    if os.path.exists(file_name):
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "Kira est une assistante vocale domotique en français."

def load_whisper_hint() -> str:
    """
    Minimal hint for Whisper — helps with home automation vocabulary.
    Includes HA entity names so Whisper transcribes them correctly.
    PAS de phrases complètes → risque d'hallucination.
    """
    base = "allume, éteins, éteindre, éteint, ouvre, ferme, coupe, active, volet, lumière, salon, escalier, entrée, LoRa, gateway, Zigbee"
    # Ajouter les noms d'entités HA si dispo (chargés après ce hint au démarrage)
    # → sera mis à jour dynamiquement après load_ha_entities()
    return base

def load_ha_entities(file_name="config/ha_entities.json") -> list[str]:
    """
    Load HA entities from the REST API at startup.

    Fallback to ha_entities.json if HA is unreachable.
    Automatically caches in ha_entities.json for the next offline startup.
    """
    try:
        from services.ha_entities_loader import load_ha_entities as _dynamic_load
        return _dynamic_load(fallback_file=file_name)
    except ImportError:
        print(f"⚠️  services.ha_entities_loader not found — fallback to {file_name}")
        if not os.path.exists(file_name):
            print(f"⚠️  {file_name} not found — fuzzy matching disabled.")
            return []
        with open(file_name, "r", encoding="utf-8") as f:
            data = json.load(f)
        entities = [e for e in data.get("entities", []) if not e.startswith("_")]
        print(f"✅ {len(entities)} HA entity(ies) loaded (static file)")
        return entities


def load_satellites_config(file_name="config/satellites.json") -> tuple[dict, list[str]]:
    """
    Load :
    - the token table → {id, room, location}
    - the list of known rooms (for injection detection in HA commands)

    Format satellites.json :
    {
      "rooms": ["salon", "chambre", "cuisine", ...],   ← known rooms in the house
      "satellites": [
        { "token": "...", "id": 1, "room": "entrée", "location": "hall d'entrée" },
        ...
      ]
    }
    """
    if not os.path.exists(file_name):
        print(f"⚠️  File {file_name} not found — satellite authentication disabled.")
        return {}, []
    with open(file_name, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Table satellites
    table = {}
    for sat in data.get("satellites", []):
        table[sat["token"]] = {
            "id":       sat["id"],
            "room":     sat["room"],
            "location": sat.get("location", sat["room"]),
        }

    # List of known rooms
    rooms = data.get("rooms", [])

    # Load chat clients as well (kira-web, kira-chat CLI, etc.)
    for client in data.get("chat_clients", []):
        tok = client.get("token", "")
        if tok:
            table[tok] = {
                "id":       client.get("id", -1),
                "room":     client.get("default_room", "chat"),
                "location": client.get("description", "chat interface"),
                "is_chat":  True,
                "name":     client.get("name", "chat"),
            }
            print(f"✅ chat client loaded: {client.get('name','?')} (token {tok[:8]}...)")

    sat_count = len([v for v in table.values() if not v.get("is_chat")])
    print(f"✅ {sat_count} satellite(s) loaded: {[v['room'] for v in table.values() if not v.get('is_chat')]}")
    print(f"✅ {len(rooms)} known room(s): {rooms}")
    return table, rooms

SYSTEM_PROMPT_FULL = load_prompt_from_file(f"prompt_{LANG.language}.txt")  # For the LLM (full)
WHISPER_HINT       = load_whisper_hint()        # For Whisper (minimal, anti-hallucination)
SATELLITES_TABLE, HA_ROOMS = load_satellites_config()
HA_ENTITIES = load_ha_entities()

# Map friendly_name → entity_id
try:
    from services.ha_entities_loader import get_entity_id_map as _get_map
    HA_ENTITY_ID_MAP = _get_map()
    print(f"✅ {len(HA_ENTITY_ID_MAP)} entity_id mapped")
except Exception as _e:
    HA_ENTITY_ID_MAP = {}
    print(f"⚠️  entity_id_map : {_e}")

# Map alias → entity_id (from core.entity_registry)
try:
    from services.ha_entities_loader import get_alias_map as _get_alias_map
    HA_ALIAS_MAP = _get_alias_map()
    print(f"✅ {len(HA_ALIAS_MAP)} alias loaded")
except Exception as _e:
    HA_ALIAS_MAP = {}
    print(f"⚠️  alias_map : {_e}")

# Map room → entities in this room (to resolve "lamp" → "Entrance Lamp")
try:
    from services.ha_entities_loader import get_room_entity_map as _get_room_map
    HA_ROOM_ENTITY_MAP = _get_room_map()
except Exception as _e:
    HA_ROOM_ENTITY_MAP = {}
    print(f"⚠️  room_entity_map : {_e}")

# Room groups — voice commands that control all lights in a room
# "turn on bedroom 1" → turns on/off all lights in bedroom 1
# Loaded from HA_ALIAS_MAP: if "bedroom 1" is a HA group → direct
# Otherwise, use the list of entity_id below
# Room groups from config/room_groups.yaml via ROOM_GROUPS
ROOM_LIGHT_GROUPS = {}
if ROOM_GROUPS and ROOM_GROUPS.groups:
    for _alias, _eids in ROOM_GROUPS.groups.items():
        ROOM_LIGHT_GROUPS[_alias] = _eids if isinstance(_eids, list) else [_eids]
        
print(f"✅ {len(ROOM_LIGHT_GROUPS)} room light group(s) configured")

# Proactive scheduler from kira.yaml → proactive.enabled
if KIRA.proactive_enabled:
    try:
        from services.proactive import start_scheduler as _start_scheduler
        _proactive_scheduler = _start_scheduler(SATELLITES_TABLE)
    except Exception as _e:
        print(f"⚠️  Proactive scheduler not started: {_e}")
else:
    print("ℹ️  Proactive announcements disabled (kira.yaml → proactive.enabled: false)")

# Enrich Whisper hint with HA entity names
# Examples: "Lampe Petit Salon, Volet Salon" → Whisper transcribes proper names better
if HA_ENTITIES:
    # Filter out first and last names from the Whisper hint
    # to prevent Whisper from hallucinating HA commands with first names
    FAMILY_NAMES = set(PERSONAS.family_names)
    safe_entities = [
        e for e in HA_ENTITIES
        if not any(name in e.lower() for name in FAMILY_NAMES)
    ]
    # Also include aliases (short terms like "petit salon", "chambre un")
    alias_terms = [
        k for k in HA_ALIAS_MAP.keys()
        if not any(name in k for name in FAMILY_NAMES)
        and len(k) > 2
    ] if HA_ALIAS_MAP else []
    all_hints    = sorted(set(list(safe_entities[:10]) + alias_terms[:15]))[:20]
    entity_hints = ", ".join(all_hints)
    WHISPER_HINT = f"allume, éteins, ouvre, ferme, coupe, active, volet, lumière, LoRa, gateway, Zigbee, {entity_hints}"
    print(f"✅ Whisper hint : {len(safe_entities)} entités + {len(alias_terms)} alias")  # token → {id, room, location}  +  liste pièces

# ──────────────────────────────────────────────
# SATELLITE CONVERSATIONAL MEMORY
# Key = satellite_id, Value = deque of messages
# ──────────────────────────────────────────────
MEMORY_SIZE = 10   # Number of turns retained (user + assistant)

def get_memory(satellite_id: int) -> deque:
    if satellite_id not in CONVERSATION_MEMORY:
        CONVERSATION_MEMORY[satellite_id] = deque(maxlen=MEMORY_SIZE * 2)
    return CONVERSATION_MEMORY[satellite_id]

def push_memory(satellite_id: int, role: str, content: str):
    mem = get_memory(satellite_id)
    mem.append({"role": role, "content": content})

CONVERSATION_MEMORY: dict[int, deque] = {}

# ──────────────────────────────────────────────
# DYNAMIC SERVICES (tools)
# ──────────────────────────────────────────────
SERVICES_DYNAMIQUES = {}

def charger_capacites():
    """
    Loads tools from config/tools_config_<lang>.json. 
    Format: {"active_tools": [...], "definitions": [...]}
    """
    
    lang = LANG.language
    tools_file = f"config/tools_config_{lang}.json"

    if not os.path.exists(tools_file):
        raise FileNotFoundError(
            f"Missing tools config file for language '{lang}': {tools_file}"
        )

    print(f"✅ Tools config: {tools_file}")

    with open(tools_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    active_tools = config["active_tools"]
    definitions = config["definitions"]

    tools_ollama = []

    for tool_def in definitions:
        name = tool_def["function"]["name"]

        if name not in active_tools:
            continue

        try:
            module = importlib.import_module(f"services.{name}")
            SERVICES_DYNAMIQUES[name] = module.run
            tools_ollama.append(tool_def)
            print(f"✅ Loaded capacity: {name}")
        except Exception as e:
            raise RuntimeError(
                f"Failed to load service '{name}'"
            ) from e

    return tools_ollama

KIRA_TOOLS = charger_capacites()

# ──────────────────────────────────────────────
# ENVIRONMENT — secrets only from .env
# All functional parameters are in kira.yaml
# ──────────────────────────────────────────────
os.environ["PATH"] += os.pathsep + "/opt/homebrew/bin"
# load_dotenv() already called at the top of the file

# Secrets (never in kira.yaml)
API_TOKEN = os.getenv("KIRA_API_TOKEN")
HA_TOKEN  = os.getenv("HA_TOKEN")
HA_URL    = os.getenv("HA_URL")

# Functional parameters from kira.yaml
USE_LLM = 1 if KIRA.llm_enabled else 0

# Keep the LLM model loaded in memory — avoids +4s cold start
if USE_LLM == 1:
    try:
        import requests as _req
        _llm_model = os.getenv("LLM_MODEL", "qwen2.5:3b")
        _req.post("http://localhost:11434/api/generate",
                  json={"model": _llm_model, "keep_alive": -1, "prompt": ""},
                  timeout=5)
        print(f"✅ Ollama keep_alive activated: {_llm_model}")
    except Exception as _e:
        print(f"⚠️  Ollama keep_alive : {_e}")

# Name variants from kira.yaml → personas.assistant_variants
MOTS_KIRA = PERSONAS.assistant_variants

app = FastAPI()

# ──────────────────────────────────────────────
# SATELLITE AUTHENTICATION
# ──────────────────────────────────────────────

def authenticate_satellite(request: Request) -> dict | None:
    """
    Check X-Token in the headers.
    Returns the satellite dict {id, room, location} if known, None otherwise.

    Modes :
      - satellites.json configured → lookup in SATELLITES_TABLE
      - satellites.json absent    → fallback global token KIRA_API_TOKEN (unknown room)
    """
    token = request.headers.get("X-Token", "")

    # Diagnostic: logs the first 8 characters of the received token (never the complete token)
    token_preview = token[:8] + "..." if len(token) > 8 else f"[empty:{len(token)} chars]"

    # ── Case 1: satellites.json loaded ──
    if SATELLITES_TABLE:
        sat = SATELLITES_TABLE.get(token)
        if sat:
            return sat
        # Unknown token → detailed log for diagnostics
        known_previews = [t[:8] + "..." for t in SATELLITES_TABLE.keys()]
        print(f"⚠️  Unknown token: received={token_preview} | known={known_previews}")
        print(f"   Received token length: {len(token)} chars")
        print(f"   X-Token header present: {'X-Token' in request.headers}")
        print(f"   All headers: {dict(request.headers)}")
        return None

    # ── Case 2: no satellites.json → fallback to old behavior ──
    print(f"⚠️  SATELLITES_TABLE empty — fallback global token")
    if token == API_TOKEN:
        print(f"   Global token accepted → room=unknown")
        return {"id": 0, "room": "unknown", "location": "unknown"}

    print(f"   Global token rejected: received={token_preview}")
    return None

# ──────────────────────────────────────────────
# HOME ASSISTANT
# ──────────────────────────────────────────────

# Whisper phonetic corrections → correct technical terms that are often misheard by Whisper.
# Whisper often confuses technical proper nouns with common words
# Phonetic corrections from config/phonetic.yaml via PHONETIC
WHISPER_PHONETIC_FIXES = PHONETIC.corrections


def normalize_for_ha(text: str, room: str = "") -> str:
    """
    Normalizes the text transcribed by Whisper to maximize the chances
    that HA finds the correct entity.

    Applied corrections:
    1. Contextual resolution by room: "allume lampe" + room="entrée"
       → "allume, Lampe Entrée" (without needing to specify the room)
    2. Fuzzy-match on HA_ENTITIES: corrects plurals and Whisper errors
    3. Phonetic corrections (Loire→LoRa, etc.)

    Examples:
      "allume lampe" (room=entrée)  → "allume, Lampe Entrée"
      "éteins, lampes petit salon"  → "éteins, Lampe Petit Salon"
      "allume lampe salon"          → "allume Lampe Salon"
    """
    if not HA_ENTITIES:
        return text   # No entities loaded → send as is

    # ──------------------------- Direct resolution by alias ──-------------------------
    # Aliases defined in HA (Assist) are the source of truth
    # "petit salon" → switch.lampe_petit_salon directly, without fuzzy-match
    if HA_ALIAS_MAP:
        # SSeparate action and entity
        parts = text.split(",", 1)
        action      = parts[0].strip()
        entity_part = parts[1].strip() if len(parts) > 1 else text

        entity_lower = entity_part.lower().strip()

        # Look for the alias in the map
        if entity_lower in HA_ALIAS_MAP:
            entity_id = HA_ALIAS_MAP[entity_lower]
            # Retrieve the friendly_name from entity_id for HA /conversation/process
            friendly = next(
                (k for k, v in HA_ALIAS_MAP.items() if v == entity_id
                 and k == entity_lower),
                entity_part
            )
            print(f"  HA alias : '{entity_part}' → {entity_id}")
            # Send action + original alias — HA Assist understands aliases
            return f"{action}, {entity_part}" if len(parts) > 1 else text

        # Look also without articles
        import re as _re_alias
        entity_no_article = _re_alias.sub(r"^(le |la |les |l'|un |une )", "", entity_lower).strip()
        if entity_no_article in HA_ALIAS_MAP:
            entity_id = HA_ALIAS_MAP[entity_no_article]
            print(f"  HA alias (without article) : '{entity_part}' → {entity_id}")
            return f"{action}, {entity_no_article}" if len(parts) > 1 else entity_no_article

    # ──------------------------- Resolution by room ──-------------------------
    # If the command is short and ambiguous (e.g., "allume lampe", "éteins lumière")
    # and we know the room of the satellite → use the entities of that room
    lower_text = text.lower()
    generic_terms = ["lampe", "lumière", "volet", "store", "prise", "chauffage", "clim"]
    is_generic    = any(t in lower_text for t in generic_terms)
    has_room_hint = any(r in lower_text for r in HA_ROOMS)  # e.g., "salon" already in the text

    if is_generic and not has_room_hint and room and room in HA_ROOM_ENTITY_MAP:
        room_entities = HA_ROOM_ENTITY_MAP[room]
        # Search for the right domain according to the generic term used:
        domain_filter = None
        if any(t in lower_text for t in ["lampe", "lumière", "spot", "led"]):
            domain_filter = "light"
        elif any(t in lower_text for t in ["volet", "store", "rideau"]):
            domain_filter = "cover"
        elif any(t in lower_text for t in ["prise", "interrupteur"]):
            domain_filter = "switch"
        elif any(t in lower_text for t in ["chauffage", "thermostat", "clim"]):
            domain_filter = "climate"

        candidates = [
            e for e in room_entities
            if domain_filter is None or e["domain"] == domain_filter
        ]

        if len(candidates) == 1:
            # Only one matching entity → certain match
            matched_name = candidates[0]["name"]
            # Extract the action verb
            import re as _re2
            verb = text.strip().split()[0] if text.strip() else ""
            print(f"  HA room resolve [{room}] : '{text}' -> '{verb}, {matched_name}'")
            return f"{verb}, {matched_name}"
        elif len(candidates) > 1:
            _best = None
            for _cand in candidates:
                _cname = _cand["name"].lower()
                if _cname in lower_text:
                    _best = _cand
                    break
            # Vérifier aussi dans HA_ALIAS_MAP
            if not _best and HA_ALIAS_MAP:
                for _alias, _eid in sorted(HA_ALIAS_MAP.items(), key=lambda x: len(x[0]), reverse=True):
                    if _alias in lower_text:
                        # Trouver le candidat correspondant
                        for _cand in candidates:
                            if _cand["entity_id"] == _eid:
                                _best = _cand
                                break
                        if _best:
                            break
                matched_name = _best["name"] if _best else candidates[0]["name"]
                verb = text.strip().split()[0] if text.strip() else ""
                print(f"  HA room resolve [{room}] multi : '{text}' -> '{verb}, {matched_name}'")
                return f"{verb}, {matched_name}"

    # Apply Whisper phonetic corrections
    import re as _re
    text_fixed = text
    for wrong, correct in PHONETIC.corrections.items():
        text_fixed = _re.sub(rf"(?i)\b{_re.escape(wrong)}\b", correct, text_fixed)
    if text_fixed != text:
        print(f"  Phonetic correction : '{text}' → '{text_fixed}'")
        text = text_fixed

    import re
    from difflib import get_close_matches

    # Separate the verb/action from the entity name (split at the comma)
    # "Turn off, Small Living Room Lamp" → action="turn off", entity_part="Small Living Room Lamp"
    parts = text.split(",", 1)
    action      = parts[0].strip()
    entity_part = parts[1].strip() if len(parts) > 1 else ""

    if not entity_part:
        # No comma → try on the whole text after the first word
        words = text.split(" ", 1)
        action      = words[0]
        entity_part = words[1].strip() if len(words) > 1 else ""

    if not entity_part:
        return text

    # Normalization of the entity part:
    # 1. Remove articles ("les ", "le ", "la ", "l'", "des ")
    entity_clean = re.sub(r"(?i)^(les?|la|l'|des?)\s+", "", entity_part).strip()

    # 2. Fuzzy match on known entities (case-insensitive)
    entity_lower  = entity_clean.lower()
    entities_lower = {e.lower(): e for e in HA_ENTITIES}

    # Attempting an exact match first
    if entity_lower in entities_lower:
        matched = entities_lower[entity_lower]
        print(f"  HA normalize (exact): '{entity_part}' → '{matched}'")
        return f"{action}, {matched}"

    # Fuzzy match : search for the nearest match
    close = get_close_matches(entity_lower, entities_lower.keys(), n=1, cutoff=0.6)
    if close:
        matched = entities_lower[close[0]]
        print(f"  HA normalize (fuzzy {close[0]!r}): '{entity_part}' → '{matched}'")
        return f"{action}, {matched}"

    # No match → return the original text (HA will still try)
    print(f"  HA normalize (no match): '{entity_part}' — sending as is")
    return text


def ask_home_assistant(text: str, room: str) -> dict:
    """
    Send the command to HA while preserving the case and Whisper syntax.
    HA /conversation/process is case-insensitive and knows its entities.
    Only the wake-word is cleaned, the room is NOT injected into the text
    (HA handles better with the exact entity name than with 'in the living room').
    """
    # Interface chat → pas de room resolve forcé
    if room in ("chat", ""):
        room = ""

    url     = f"{HA_URL}/conversation/process"
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

    import re

    # 1. Clean the wake-word (case-insensitive, preserve the case of the rest)
    text_for_ha = text
    for mot in MOTS_KIRA:
        text_for_ha = re.sub(rf'(?i)\b{mot}\b[,]?\s*', '', text_for_ha).strip()

    # 2. Fuzzy-match HA entities to correct plurals / Whisper errors
    text_for_ha = normalize_for_ha(text_for_ha, room=room)
    # Clean double commas
    import re as _re_clean
    text_for_ha = _re_clean.sub(',  *,', ',', text_for_ha).strip()
    text_for_ha = text_for_ha.rstrip('.,') + ''  # keep punctuation clean

    # ──-------------------------Group command detection (entire room) ──-------------------------
    # "turn on living room" → turn on all lights in living room
    # Build the room → entity_ids map from HA_ALIAS_MAP
    # Exclude network/POE/switch infrastructure entities from group control
    EXCLUDED_ENTITY_PREFIXES = [
        "switch.switch", "switch.poe", "switch.port",
        "switch.router", "switch.hub", "switch.nas",
    ]
    # Keywords to exclude — cameras, sensors, alarms that are not lights
    EXCLUDED_KEYWORDS = [
        "camera", "sensor", "motion", "door", "window",
        "alarm", "siren", "buzzer", "temperature", "lora",
        "humidity", "co2", "presence", "smoke",
    ]

    group_triggers = {}
    if HA_ALIAS_MAP and HA_ROOM_ENTITY_MAP:
        for room_name, entities in HA_ROOM_ENTITY_MAP.items():
            light_ids = [
                e["entity_id"] for e in entities
                if e["domain"] in ("light", "switch")
                and not any(e["entity_id"].startswith(p) for p in EXCLUDED_ENTITY_PREFIXES)
                and not any(kw in e["entity_id"].lower() for kw in EXCLUDED_KEYWORDS)
            ]
            if len(light_ids) > 1:
                group_triggers[room_name] = light_ids

    # Check if the command targets an entire room
    ha_verbs = ["allume", "éteins", "active", "désactive", "coupe"]
    text_lower = text_for_ha.lower()
    action_verb = next((v for v in ha_verbs if v in text_lower), None)

    if action_verb and group_triggers:
        # If a specific alias exists (e.g., "lampe salon") → do not use the group
        alias_match = any(
            alias in text_lower and len(alias) > 3
            for alias in HA_ALIAS_MAP.keys()
        ) if HA_ALIAS_MAP else False

        if not alias_match:
            for room_name, entity_ids in group_triggers.items():
                if room_name in text_lower:
                    print(f"  HA groupe [{room_name}] : {action_verb} {len(entity_ids)} lampe(s)")
                    service = "turn_on" if action_verb in ("allume", "active") else "turn_off"
                    results = []
                    for eid in entity_ids:
                        domain = eid.split(".")[0]
                        try:
                            r_group = requests.post(
                                f"{HA_URL.rstrip('/api')}/api/services/{domain}/{service}",
                                headers={"Authorization": f"Bearer {HA_TOKEN}",
                                         "Content-Type": "application/json"},
                                json={"entity_id": eid},
                                timeout=5
                            )
                            results.append(r_group.status_code == 200)
                        except Exception as _e:
                            print(f"  HA groupe erreur {eid}: {_e}")
                            results.append(False)
                    action_fr = "allumées" if service == "turn_on" else "éteintes"
                    count_ok  = sum(results)
                    speech = (
                        f"Les lumières de {room_name} sont {action_fr}."
                        if count_ok == len(entity_ids)
                        else f"{count_ok} lumière(s) sur {len(entity_ids)} {action_fr}."
                    )
                    print(f"  HA group résultat : {speech}")
                    return {"success": True, "speech": speech}
    print(f"→ HA [{room}] : '{text_for_ha}'")
    try:
        r = requests.post(url, headers=headers, json={"text": text_for_ha, "language": "fr"}, timeout=5)
        if r.status_code != 200:
            print(f"DEBUG HA : Code {r.status_code} - {r.text}")
            return {"success": False, "speech": f"Erreur HA {r.status_code}"}
        response_obj = r.json().get("response", {})
        speech  = response_obj.get("speech", {}).get("plain", {}).get("speech", "Fait.")
        success = response_obj.get("response_type", "") not in ("error", "failed")
        if not success:
            print(f"DEBUG HA : response_type={response_obj.get('response_type')} | speech='{speech}'")
            # HA cannot resolve → try with "all" to target the group
            if "plusieurs" in speech.lower() or "ambig" in speech.lower():
                print(f"  HA ambiguity detected — retry with 'all'")
                retry_text = text_for_ha.rsplit(',', 1)[0] + f', all lights {room}'
                try:
                    r2 = requests.post(url, headers=headers,
                                       json={"text": retry_text, "language": "fr"},
                                       timeout=5)
                    if r2.status_code == 200:
                        resp2 = r2.json().get("response", {})
                        speech2  = resp2.get("speech", {}).get("plain", {}).get("speech", speech)
                        success2 = resp2.get("response_type", "") not in ("error", "failed")
                        if success2:
                            print(f"  HA retry OK : {retry_text}")
                            return {"success": True, "speech": speech2}
                except Exception:
                    pass
        return {"success": success, "speech": speech}
    except Exception as e:
        print(f"DEBUG HA : Connection error : {e}")
        return {"success": False, "speech": "The HA is not responding."}

# ──────────────────────────────────────────────
# TRANSCRIPTION
# ──────────────────────────────────────────────

def transcribe_audio(audio_data: bytes) -> str | None:
    """Transcribes audio via the configured STT backend. Returns None if only noise is present."""
    return stt.transcribe(audio_data, initial_prompt=WHISPER_HINT)

# ──────────────────────────────────────────────
# ROUTING + EXÉCUTION (One single call Ollama)
# ──────────────────────────────────────────────

# The system prompt LLM = content of prompt.txt + dynamic context (date + room)
# The prompt.txt is the source of truth for Kira's personality.
# Suffix loaded from prompt_suffix.txt — contains {date}, {room}, {location},
# {language_name} and {default_city} dynamically replaced on each LLM call.


def _load_suffix() -> str:
    """
    Load the system suffix from prompt_suffix_<lang>.txt.
    Fallback: prompt_suffix.txt, then minimal value.
    """
    lang = getattr(LANG, "language", "fr")
    # Try the language-specific file first
    for candidate in [f"prompt_suffix_{lang}.txt", "prompt_suffix.txt"]:
        raw = load_prompt_from_file(candidate)
        if raw:
            print(f"✅ Suffix prompt : {candidate}")
            return raw
    # Fallback minimal multilingue
    print("⚠️  prompt_suffix_<lang>.txt absent — fallback minimal")
    return """{date} | {room}
Respond ALWAYS in {language_name}. No markdown. One sentence max two.
Use tools for HA commands and weather. Never comment on tool usage.
"""

SYSTEM_PROMPT_SUFFIX = _load_suffix()

def detect_expect_reply(reply: str) -> bool:
    """
    Detects whether Kira's response is a question requiring a reply from the user.
    Short responses (confirmations) are never questions.

    The ESP32 uses this flag to immediately listen again without a wake word.

    RRules:
      - Response < 15 chars → False (ex: "On", "Off", "Done.")
      - Ends with "?" → True
      - Contains typical interrogative words → True
      - Empty or pure confirmation → False
    """
    if not reply or len(reply.strip()) < 15:
        return False
    lower = reply.strip().lower()
    if any(p in lower for p in LANG.no_reply_patterns):
        return False
    if reply.strip().endswith("?"):
        return True
    import re
    return any(re.search(p, lower) for p in LANG.question_regex_patterns)




def router_llm(user_text: str, satellite: dict) -> tuple[str, str, bool]:
    """
    Single LLM call with system prompt, satellite history, and tools.
    Returns (category, reply_content, expect_reply).
    """
    now      = datetime.now().strftime("%d/%m/%Y %H:%M")
    room     = satellite["room"]
    location = satellite.get("location", room)
    sat_id   = satellite["id"]

    # Speaker context from LANG (multilingual)
    speaker_ctx = ""
    if satellite.get("speaker"):
        spk  = satellite["speaker"]
        conf = satellite.get("speaker_confidence", 0)
        speaker_ctx = LANG.speaker_context.format(name=spk, conf=f"{conf:.0%}")

    system_msg = {
        "role": "system",
        "content": SYSTEM_PROMPT_FULL + memory.build_memory_context() + speaker_ctx +
                   SYSTEM_PROMPT_SUFFIX.format(
                       date=now, room=room, location=location,
                       language_name=LANG.language_name,
                       default_city=KIRA.weather_default_city,
                   )
    }

    history  = list(get_memory(sat_id))
    messages = [system_msg] + history + [{"role": "user", "content": user_text}]

    response = llm.chat(messages, tools=KIRA_TOOLS)

    # ── Case 1: LLM wants to use a tool ──
    if response.get("tool_calls"):
        return _execute_tools(response, messages, sat_id)

    # ── Case 2: direct textual response ──
    reply = response.get("content", "").strip()
    lower = user_text.lower()

    # Protected names → always SPEECH
    is_about_person = any(name in lower for name in PERSONAS.family_names)
    is_question     = any(q in lower for q in LANG.person_question_patterns)

    if is_about_person or is_question:
        category = "SPEECH"
    else:
        category = "HA" if any(kw in lower for kw in LANG.ha_action_verbs) else "SPEECH"

    if category == "HA":
        push_memory(sat_id, "user",      user_text)
        push_memory(sat_id, "assistant", reply)
        return "HA", "", False

    push_memory(sat_id, "user",      user_text)
    push_memory(sat_id, "assistant", reply)
    memory.save_exchange(sat_id, user_text, reply)
    if memory.maybe_extract_fact(user_text):
        memory.save_fact(reply, source="auto")
    return "SPEECH", reply, detect_expect_reply(reply)


def _execute_tools(response: dict, messages: list, sat_id: int) -> tuple[str, str, bool]:
    """Executes the tool_calls and returns the final response."""
    for call in response.get("tool_calls", []):
        # Normalisation : Ollama and llama_cpp have different structures
        if hasattr(call, "function"):          # objet Ollama
            func_name = call.function.name
            args      = call.function.arguments
        else:                                  # dict llama_cpp / OpenAI
            func_name = call["function"]["name"]
            args      = call["function"]["arguments"]

        if func_name not in SERVICES_DYNAMIQUES:
            return "SPEECH", LANG.tool_unknown, False

        print(f"🛠️  Kira utilise : {func_name}({args})")
        resultat = SERVICES_DYNAMIQUES[func_name](**args)

        # Tools that return natural text → direct response without LLM
        DIRECT_REPLY_TOOLS = {"get_ha_state", "get_weather", "web_search"}
        if func_name in DIRECT_REPLY_TOOLS:
            reply = str(resultat).strip()
            if not reply or len(reply) < 3:
                reply = LANG.tool_no_result
        else:
            # Other tools → LLM reformulation in the correct language
            lang_system = {
                "role":    "system",
                "content": LANG.tool_reformulation.format(
                    language_name=LANG.language_name)
            }
            followup_msgs = [lang_system] + messages[1:] + [
                {"role": "assistant", "content": "", "tool_calls": response["tool_calls"]},
                {"role": "tool",      "content": str(resultat), "name": func_name}
            ]
            followup = llm.chat(followup_msgs)
            reply    = followup.get("content", "").strip()

        push_memory(sat_id, "user",      messages[-1]["content"])
        push_memory(sat_id, "assistant", reply)
        memory.save_exchange(sat_id, messages[-1]["content"], reply)
        return "SPEECH", reply, detect_expect_reply(reply)

    return "SPEECH", "", False



def personalize(reply: str, satellite: dict) -> str:
    """Adds the first name if the speaker is identified — delegates to LANG.personalize()"""
    spk = satellite.get("speaker", "")
    if not spk or not reply:
        return reply
    return LANG.personalize(reply, spk)


def execute_category(category: str, user_text: str, satellite: dict) -> tuple[str, str]:
    """Exécution des catégories HA (et fallback si LLM désactivé)."""
    if category == "HA":
        ha_res = ask_home_assistant(user_text, satellite["room"])
        reply  = personalize(ha_res["speech"], satellite)
        sat_id = satellite["id"]
        push_memory(sat_id, "user",      user_text)
        push_memory(sat_id, "assistant", reply)
        memory.save_exchange(sat_id, user_text, reply)
        return reply, "ok" if ha_res["success"] else "error"

    # SPEECH without LLM enabled → default response from the language
    return LANG.idle_response, "no_action"

# ──────────────────────────────────────────────
# MAIN ENDPOINT
# ──────────────────────────────────────────────

@app.post("/transcribe")
async def process_kira(request: Request):
    # Auth satellite
    satellite = authenticate_satellite(request)
    if satellite is None:
        raise HTTPException(status_code=403, detail="Invalid satellite token")

    _src = "💬 Chat" if satellite.get("id") == -1 else f"📍 Satellite"
    print(f"\n{_src} from : {satellite['room']} (id={satellite['id']})")

    import time as _time
    _t_start   = _time.time()

    audio_data = await request.body()
    print(f"  ⏱ Audio received : {len(audio_data):,} bytes")

    try:
        _t0       = _time.time()
        user_text = transcribe_audio(audio_data)
        print(f"  ⏱ Whisper STT    : {_time.time()-_t0:.2f}s")

        if user_text is None:
            print("STT : noise only, ignored.")
            return {"status": "ignored", "reason": "noise"}

        print(f"🎤 [{satellite['room']}] {user_text}")
        memory.track_query(user_text, extra={"room": satellite.get("room", "")})

        category      = "SPEECH"
        reply_content = ""
        ha_ack        = "no_action"
        expect_reply  = False

        # ── Speaker identification (non blocking — just for personalization) ─
        _speaker_name = None
        _speaker_conf = 0.0
        if speaker_backend and KIRA.speaker_enabled:
            try:
                _speaker_name, _speaker_conf = speaker_backend.identify(audio_data)
                if _speaker_name:
                    print(f"  🎙️  Speaker : {_speaker_name} ({_speaker_conf:.0%})")
                else:
                    print(f"  🎙️  Unknown speaker ({_speaker_conf:.0%})")
            except Exception as _se:
                print(f"  🎙️  Speaker error (ignored) : {_se}")

        # Enrich satellite with the first name for all bypasses and the LLM
        if _speaker_name:
            satellite = dict(satellite)
            satellite["speaker"]            = _speaker_name
            satellite["speaker_confidence"] = _speaker_conf

        # ------------------------- Bypass LLM : sensors and device states via alias -------------------------
        lower_pre = user_text.lower()
        is_sensor_q = (any(kw in lower_pre for kw in LANG.sensor_question_keywords)
                       and any(q in lower_pre for q in LANG.sensor_question_markers))
        is_state_q  = any(kw in lower_pre for kw in LANG.state_question_keywords)

        # Direct detection: known alias + question marker
        # Covers "I have mail?", "Is there a package?", "Mail?""
        if not is_sensor_q and not is_state_q and HA_ALIAS_MAP:
            if any(q in lower_pre for q in LANG.question_markers):
                _lower_clean = LANG.normalize_text(lower_pre)
                for _alias in sorted(HA_ALIAS_MAP.keys(), key=len, reverse=True):
                    if (_alias in lower_pre or _alias in _lower_clean) and len(_alias) > 2:
                        _eid = HA_ALIAS_MAP[_alias]
                        if _eid.startswith(("sensor.", "binary_sensor.")):
                            is_sensor_q = True
                            break

        if (is_sensor_q or is_state_q) and HA_ALIAS_MAP:
            _matched_eid   = None
            _matched_alias = None
            # Normalize before lookup:
            # 1. Synonyms: "lumière" → "lampe"
            # 2. Remove articles: "la lampe" → "lampe", "le volet" → "volet"
            import re as _re_norm
            lower_normalized = LANG.normalize_text(lower_pre)
            lower_no_article = lower_normalized

            for _alias in sorted(HA_ALIAS_MAP.keys(), key=len, reverse=True):
                if (_alias in lower_pre
                        or _alias in lower_normalized
                        or _alias in lower_no_article):
                    _eid = HA_ALIAS_MAP[_alias]
                    if is_sensor_q and _eid.startswith(("sensor.", "binary_sensor.")):
                        _matched_eid, _matched_alias = _eid, _alias; break
                    elif is_state_q and not _eid.startswith("sensor."):
                        _matched_eid, _matched_alias = _eid, _alias; break
            if _matched_eid:
                try:
                    from services.get_ha_state import run as _gs
                    _result = _gs(_matched_eid)
                    _qtype  = "capteur" if is_sensor_q else "état"
                    print(f"  ⚡ Bypass {_qtype} : '{_matched_alias}' → {_matched_eid}")
                    _sat_id = satellite["id"]
                    push_memory(_sat_id, "user", user_text)
                    push_memory(_sat_id, "assistant", _result)
                    memory.save_exchange(_sat_id, user_text, _result)
                    memory.track_query(user_text, extra={"room": satellite.get("room", "")})
                    print(f"  ⏱ TOTAL /transcribe : {_time.time()-_t_start:.2f}s")
                    # Retourner directement — pas besoin de router_llm
                    reply_content = personalize(_result, satellite)
                    expect_reply  = False
                    ha_ack        = "no_action"
                    category      = "SPEECH"
                    tts_available = tts_backend.backend_name() != "none"
                    return {
                        "status":        "success",
                        "heard":         user_text,
                        "room":          satellite["room"],
                        "reply":         reply_content,
                        "ha_ack":        ha_ack,
                        "category":      category,
                        "expect_reply":  expect_reply,
                        "tts_available": tts_available,
                    }
                except Exception as _e:
                    print(f"  Bypass état erreur : {_e}")

        # ------------------------ Bypass time and date -------------------------
        # MUST be before is_state_words because "est-il" in "heure est-il" breaks the regex
        if any(kw in lower_pre for kw in LANG.time_keywords):
            _now = datetime.now()
            _h, _m = _now.hour, _now.minute
            if any(kw in lower_pre for kw in LANG.time_hour_keywords):
                _result = LANG.time_reply(_h, _m)
            elif any(kw in lower_pre for kw in LANG.time_date_keywords):
                _result = LANG.date_format.format(
                    weekday=LANG.weekdays[_now.weekday()],
                    day=_now.day, month=LANG.months[_now.month-1], year=_now.year)
            else:
                _result = LANG.datetime_format.format(
                    hour=_h, minute=str(_m).zfill(2),
                    weekday=LANG.weekdays[_now.weekday()],
                    day=_now.day, month=LANG.months[_now.month-1], year=_now.year)
            _result = personalize(_result, satellite)
            print(f"  ⚡ Bypass heure : {_result}")
            push_memory(satellite["id"], "user", user_text)
            push_memory(satellite["id"], "assistant", _result)
            memory.save_exchange(satellite["id"], user_text, _result)
            print(f"  ⏱ TOTAL /transcribe : {_time.time()-_t_start:.2f}s")
            return {
                "status": "success", "heard": user_text,
                "room": satellite["room"], "reply": _result,
                "ha_ack": "no_action", "category": "SPEECH", "expect_reply": False,
            }

        # ------------------------ Weather Bypass -------------------------
        if any(kw in lower_pre for kw in LANG.weather_keywords):
            _city = KIRA.weather_default_city
            for _c in LANG.weather_cities:
                if _c in lower_pre: _city = _c.capitalize(); break
            _days = 2 if LANG.weather_tomorrow in lower_pre else 3 if LANG.weather_day_after in lower_pre else 1
            try:
                from services.get_weather import run as _gw
                _result = personalize(_gw(city=_city, days=_days), satellite)
                print(f"  ⚡ Weather Bypass : {_city}")
                push_memory(satellite["id"], "user", user_text)
                push_memory(satellite["id"], "assistant", _result)
                memory.save_exchange(satellite["id"], user_text, _result)
                print(f"  ⏱ TOTAL /transcribe : {_time.time()-_t_start:.2f}s")
                return {
                    "status": "success", "heard": user_text,
                    "room": satellite["room"], "reply": _result,
                    "ha_ack": "no_action", "category": "SPEECH", "expect_reply": False,
                }
            except Exception as _e:
                print(f"  Weather Bypass error : {_e}")

        # ------------------------- Pre-routing HA without LLM -------------------------
        # Direct detection by keywords BEFORE calling the LLM
        lower_pre    = user_text.lower()

        # ------------------------- Room groups -------------------------
        # "turn on bedroom 1" → control all lights in the room
        _verbs_group = LANG.ha_action_verbs
        _has_verb_g  = any(v in lower_pre for v in _verbs_group)
        if _has_verb_g:
            for _room_key, _eids in ROOM_LIGHT_GROUPS.items():
                if _room_key in lower_pre:
                    # Do not trigger if a specific light is mentioned
                    _specific = ROOM_GROUPS.specific_keywords if hasattr(ROOM_GROUPS, "specific_keywords") else ["right", "left", "table", "outside", "panel"]
                    if any(s in lower_pre for s in _specific):
                        break
                    _service = "turn_on" if any(v in lower_pre for v in ["turn on","activate"]) else "turn_off"
                    import requests as _rg
                    _headers_g = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
                    _ok = 0
                    for _eid in _eids:
                        try:
                            _r = _rg.post(f"{HA_URL}/services/{_eid.split('.')[0]}/{_service}",
                                          headers=_headers_g, json={"entity_id": _eid}, timeout=3)
                            if _r.status_code in (200, 201): _ok += 1
                        except Exception: pass
                    _msg_tpl = LANG.group_lights_on if _service == "turn_on" else LANG.group_lights_off
                    _msg = personalize(_msg_tpl.format(room=_room_key), satellite)
                    print(f"  ⚡ Groupe [{_room_key}] : {_ok}/{len(_eids)} {'on' if _service=='turn_on' else 'off'}")
                    _sid = satellite["id"]
                    push_memory(_sid, "user", user_text)
                    push_memory(_sid, "assistant", _msg)
                    memory.save_exchange(_sid, user_text, _msg)
                    memory.track_query(user_text, extra={"room": satellite.get("room","")})
                    print(f"  ⏱ TOTAL /transcribe : {_time.time()-_t_start:.2f}s")
                    return {
                        "status": "success", "heard": user_text,
                        "room": satellite["room"], "reply": _msg,
                        "ha_ack": "ok" if _ok > 0 else "error",
                        "category": "SPEECH", "expect_reply": False,
                        "tts_available": tts_backend.backend_name() != "none",
                    }

        # Bypass HA: only explicit action VERBS
        # Only entity names (Living Room Lamp, Shutter...) go to LLM
        # because Whisper may have lost the verb at the beginning of the sentence

        ha_verbs_pre = LANG.ha_action_verbs
        protected_pre = list(PERSONAS.family_names)
        is_person_pre   = any(n in lower_pre for n in protected_pre)
        is_question_pre = any(q in lower_pre for q in LANG.person_question_patterns)
        has_verb_pre    = any(v in lower_pre for v in ha_verbs_pre)

        # Do not trigger the HA bypass if it's a state question
        # Use precise patterns to avoid false positives
        # "turn off living room lamp" ≠ "is it off"
        import re as _re_state
        is_state_words = bool(_re_state.search(LANG.state_regex, lower_pre))

        if (not is_person_pre and not is_question_pre
                and has_verb_pre and not is_state_words):
            print(f"  ⚡ Bypass LLM HA direct")
            _t0 = _time.time()
            category = "HA"
            reply_content, ha_ack = execute_category("HA", user_text, satellite)
            print(f"  ⏱ LLM total      : 0.00s → category=HA (bypass)")
            print(f"  ⏱ execute_cat    : {_time.time()-_t0:.2f}s")
        elif USE_LLM == 1:
            try:
                _t0 = _time.time()

                # Speaker already identified before the bypasses — satellite enriched
                category, reply_content, expect_reply = router_llm(user_text, satellite)
                print(f"  ⏱ LLM total      : {_time.time()-_t0:.2f}s → category={category}")
            except Exception as e:
                print(f"LLM error : {e}")
                category = "SPEECH"

        if not reply_content:
            _t0 = _time.time()
            reply_content, ha_ack = execute_category(category, user_text, satellite)
            print(f"  ⏱ execute_cat    : {_time.time()-_t0:.2f}s")

        # Personalize the final response with the first name if identified
        reply_content = personalize(reply_content, satellite)

        if expect_reply:
            print(f"❓ expect_reply=True : Kira is waiting for a response")

        print(f"  ⏱ TOTAL /transcribe : {_time.time()-_t_start:.2f}s")

        return {
            "status":       "success",
            "heard":        user_text,
            "room":         satellite["room"],
            "reply":        reply_content,
            "ha_ack":       ha_ack,
            "category":     category,
            "expect_reply": expect_reply,
        }

    except Exception as e:
        print(f"Erreur globale : {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ──────────────────────────────────────────────
# UTILITY ENDPOINTS
# ──────────────────────────────────────────────

@app.get("/satellites")
async def list_satellites(request: Request):
    """Liste les satellites enregistrés (admin uniquement)."""
    if request.headers.get("X-Token") != API_TOKEN:
        raise HTTPException(status_code=403)
    return {
        "satellites": [
            {"id": v["id"], "room": v["room"], "location": v["location"]}
            for v in SATELLITES_TABLE.values()
        ]
    }

@app.delete("/memory/{satellite_id}")
async def clear_memory(satellite_id: int, request: Request):
    """Clears the history of a satellite (admin)."""
    if request.headers.get("X-Token") != API_TOKEN:
        raise HTTPException(status_code=403)
    if satellite_id in CONVERSATION_MEMORY:
        CONVERSATION_MEMORY[satellite_id].clear()
    return {"status": "cleared", "satellite_id": satellite_id}

@app.get("/debug-auth")
async def debug_auth(request: Request):
    """
    Diagnostic endpoint to check authentication without sending audio.
    Usage from the terminal:
      curl -H "X-Token: YOUR_TOKEN" http://192.168.0.13:8000/debug-auth
    From the ESP32: GET /debug-auth with the same X-Token header as /transcribe.

    """
    token = request.headers.get("X-Token", "")
    token_preview = token[:8] + "..." if len(token) > 8 else f"[empty]"

    sat = authenticate_satellite(request)

    return {
        "token_received":  token_preview,
        "token_length":    len(token),
        "header_present":  "X-Token" in request.headers,
        "authenticated":   sat is not None,
        "satellite":       sat,
        "satellites_loaded": len(SATELLITES_TABLE),
        "satellites_rooms":  [v["room"] for v in SATELLITES_TABLE.values()],
        "hint": (
            "Token recognized ✅" if sat
            else "Token not recognized ❌ — check that the ESP32 token matches the one in config/satellites.json"
        )
    }


@app.post("/tts")
async def synthesize_speech(request: Request):
    """
    Dynamic speech synthesis — returns raw WAV binary.

    Request:
      POST /tts
      X-Token: <satellite_token>
      Content-Type: text/plain
      Body: UTF-8 text to be spoken

    Response:
      Content-Type: audio/wav
      Body: WAV 16kHz mono 16-bit (header 44 bytes + raw PCM)
      → The ESP32 skips the 44 bytes and sends the rest directly via I2S
    """
    satellite = authenticate_satellite(request)
    if satellite is None:
        raise HTTPException(status_code=403, detail="Invalid satellite token")

    body = await request.body()
    text = body.decode("utf-8").strip()

    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    print(f"🔊 TTS [{satellite['room']}] : {text[:80]}")
    try:
        wav_bytes = tts_backend.synthesize(text)
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={"Content-Length": str(len(wav_bytes))},
        )
    except Exception as e:
        print(f"TTS error : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/tts/cache")
async def clear_tts_cache(request: Request):
    """Clears the TTS cache — forces regeneration of all phrases."""
    if request.headers.get("X-Token") != API_TOKEN:
        raise HTTPException(status_code=403)
    tts_backend.cache_clear()
    return {"status": "ok", "message": "TTS cache cleared"}


@app.get("/memory/stats")
async def memory_stats(request: Request):
    """
    Most frequent questions — shows what Kira has learned.
    Admin access only.

    Example response:
      {"stats": [
        {"intent": "weather", "canonical": "weather geneva", "count": 47},
        {"intent": "light", "canonical": "light living room on", "count": 23}
      ]}
    """
    if request.headers.get("X-Token") != API_TOKEN:
        raise HTTPException(status_code=403)
    return {
        "stats":         memory.get_stats(limit=20),
        "total_queries": sum(s["count"] for s in memory.get_stats(100)),
        "habits_active": bool(memory.build_adaptive_context().strip()),
    }


@app.get("/memory/facts")
async def list_facts(request: Request):
    """List all memorized facts (admin)."""
    if request.headers.get("X-Token") != API_TOKEN:
        raise HTTPException(status_code=403)
    return {"facts": memory.list_facts_with_ids()}

@app.delete("/memory/facts/{fact_id}")
async def delete_fact(fact_id: int, request: Request):
    """Delete a memorized fact (admin)."""
    if request.headers.get("X-Token") != API_TOKEN:
        raise HTTPException(status_code=403)
    memory.delete_fact(fact_id)
    return {"status": "deleted", "id": fact_id}


@app.post("/alert")
async def proactive_alert(request: Request):
    """
    Trigger a proactive voice announcement on one or more satellites.
    Called by the APScheduler scheduler or by an external HA webhook.

    Body JSON :
      {
        "text":         "Good evening, do you want to turn on the lights?",
        "room":         "living room",   // target room (optional if satellite_ip provided)
        "satellite_ip": "192.168.0.20"   // direct IP of the ESP32 (optional)
      }

    Authentication: X-Token admin only.

    Flow :
      1. Generate the WAV via Piper/ElevenLabs (tts_backend.synthesize)
      2. POST the binary WAV directly to the target ESP32
         via its /play endpoint (to be implemented on the firmware side)
      3. The ESP32 plays the WAV via I2S without wake word
    """
    token = request.headers.get("X-Token", "")
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Admin token required")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    text         = body.get("text", "").strip()
    room         = body.get("room", "unknown")
    satellite_ip = body.get("satellite_ip")

    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")

    print(f"📢 Proactive announcement [{room}] : {text[:60]}")

    # Generate the WAV
    try:
        wav_bytes = tts_backend.synthesize(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS error : {e}")

    # Find the satellite IP if not provided
    if not satellite_ip:
        for sat in SATELLITES_TABLE.values():
            if sat.get("room") == room:
                satellite_ip = sat.get("ip")
                break

    if not satellite_ip:
        # No IP → return the WAV for the caller to dispatch it themselves
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={
                "Content-Length": str(len(wav_bytes)),
                "X-Room":         room,
            },
        )

    # Send directly to the ESP32 via POST /play
    try:
        import requests as req
        r = req.post(
            f"http://{satellite_ip}/play",
            data=wav_bytes,
            headers={
                "Content-Type":   "audio/wav",
                "Content-Length": str(len(wav_bytes)),
            },
            timeout=10,
        )
        if r.status_code != 200:
            print(f"⚠️  ESP32 [{satellite_ip}] HTTP {r.status_code}")
            return {"status": "warning", "esp32_code": r.status_code}
        return {"status": "ok", "room": room, "wav_bytes": len(wav_bytes)}
    except Exception as e:
        print(f"❌ ESP32 [{satellite_ip}] error : {e}")
        raise HTTPException(status_code=502, detail=f"ESP32 unreachable : {e}")


@app.post("/chat")
async def chat_text(request: Request):
    """
    Text interface — uses EXACTLY the same pipeline as /transcribe
    but without the Whisper STT step.

    The text is injected directly after transcription,
    then process_kira_pipeline() handles everything: bypasses, LLM, HA, TTS.

    Body JSON :
      { "text": "allume lampe salon", "room": "bureau" }
    """
    # ──------------------------- Auth ──-------------------------
    token     = request.headers.get("X-Token", "")
    satellite = authenticate_satellite(request)

    if satellite is None:
        if token == API_TOKEN:
            satellite = {"id": 0, "room": "chat", "location": "text interface", "is_chat": True}
        else:
            raise HTTPException(status_code=403,
                detail="Invalid token — add the token in satellites.json -> chat_clients")

    if satellite.get("is_chat"):
        print(f"💬 Chat connection [{satellite.get('name', 'chat')}] authenticated")

    # ──------------------------- Parse body ──-------------------------
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")

    # Override room if specified in the body
    if body.get("room") and body["room"] not in ("", "chat"):
        satellite = dict(satellite)
        satellite["room"]     = body["room"]
        satellite["location"] = body["room"]

    print(f"💬 Chat [{satellite['room']}] : {text}")

    # ──------------------------- Pipeline identical to /transcribe — without Whisper ──-------------------------
    # We reuse exactly process_kira by injecting user_text directly
    try:
        result = await _run_pipeline(text, satellite)
        return result
    except Exception as e:
        import traceback
        print(f"Error /chat : {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


async def _run_pipeline(user_text: str, satellite: dict) -> dict:
    """
    Common pipeline for /transcribe and /chat.
    Called after STT in /transcribe, directly in /chat.
    Contains ALL bypasses and the LLM — a single source of truth.
    """
    import time as _time
    _t_start = _time.time()

    sat_id = satellite["id"]
    room   = satellite["room"]
    lower  = user_text.lower()

    memory.track_query(user_text, extra={"room": room})

    # ──------------------------- Bypass : sensors and device status ──-------------------------
    is_sensor_q = (any(kw in lower for kw in LANG.sensor_question_keywords)
                   and any(q in lower for q in LANG.sensor_question_markers))
    is_state_q  = any(kw in lower for kw in LANG.state_question_keywords)

    # Direct detection by alias + question
    # "j'ai du courrier ?" → "courrier" in HA_ALIAS_MAP → binary_sensor
    # Works for any sensor/detector alias
    is_question = any(q in lower for q in LANG.question_markers)

    if is_question and HA_ALIAS_MAP and not is_sensor_q and not is_state_q:
        # Search if a known alias is in the question
        for _alias in sorted(HA_ALIAS_MAP.keys(), key=len, reverse=True):
            if _alias in lower and len(_alias) > 3:
                _eid = HA_ALIAS_MAP[_alias]
                # Activate for sensors and binary_sensors only
                if _eid.startswith(("sensor.", "binary_sensor.")):
                    is_sensor_q = True
                    break

    if (is_sensor_q or is_state_q) and HA_ALIAS_MAP:
        import re as _re_norm
        lower_normalized = LANG.normalize_text(lower)
        lower_no_article = lower_normalized
        _matched_eid = _matched_alias = None
        for _alias in sorted(HA_ALIAS_MAP.keys(), key=len, reverse=True):
            if _alias in lower or _alias in lower_normalized or _alias in lower_no_article:
                _eid = HA_ALIAS_MAP[_alias]
                if is_sensor_q and _eid.startswith(("sensor.", "binary_sensor.")):
                    _matched_eid, _matched_alias = _eid, _alias; break
                elif is_state_q and not _eid.startswith(("sensor.", "binary_sensor.")):
                    _matched_eid, _matched_alias = _eid, _alias; break
        if _matched_eid:
            try:
                from services.get_ha_state import run as _gs
                _result = _gs(_matched_eid)
                print(f"  ⚡ Bypass state : '{_matched_alias}' → {_matched_eid}")
                push_memory(sat_id, "user", user_text)
                push_memory(sat_id, "assistant", _result)
                memory.save_exchange(sat_id, user_text, _result)
                print(f"  ⏱ /chat total : {_time.time()-_t_start:.2f}s")
                return _make_response(user_text, room, _result, "no_action", "SPEECH", False, satellite=satellite)
            except Exception as _e:
                print(f"  ⚡ Bypass error state : {_e}")



    # ------------------------ Bypass : time and date -------------------------
    if any(kw in lower for kw in LANG.time_keywords):
        now = datetime.now()
        _h, _m = now.hour, now.minute
        if any(kw in lower for kw in LANG.time_hour_keywords):
            result = LANG.time_reply(_h, _m)
        elif any(kw in lower for kw in LANG.time_date_keywords):
            result = LANG.date_format.format(
                weekday=LANG.weekdays[now.weekday()],
                day=now.day, month=LANG.months[now.month-1], year=now.year)
        else:
            result = LANG.datetime_format.format(
                hour=_h, minute=str(_m).zfill(2),
                weekday=LANG.weekdays[now.weekday()],
                day=now.day, month=LANG.months[now.month-1], year=now.year)
        result = personalize(result, satellite)
        print(f"  ⚡ Bypass time : {result}")
        push_memory(sat_id, "user", user_text)
        push_memory(sat_id, "assistant", result)
        return _make_response(user_text, room, result, "no_action", "SPEECH", False, satellite=satellite)

    # ------------------------ Bypass : weather -------------------------
    if any(kw in lower for kw in LANG.weather_keywords):
        city = KIRA.weather_default_city
        for c in LANG.weather_cities:
            if c in lower: city = c.capitalize(); break
        days = 2 if LANG.weather_tomorrow in lower else 3 if LANG.weather_day_after in lower else 1
        try:
            from services.get_weather import run as _gw
            result = _gw(city=city, days=days)
            print(f"  ⚡ Bypass weather : {city}")
            push_memory(sat_id, "user", user_text)
            push_memory(sat_id, "assistant", result)
            return _make_response(user_text, room, result, "no_action", "SPEECH", False, satellite=satellite)
        except Exception as e:
            print(f"  ⚡ Bypass weather error : {e}")

    # ------------------------- Pré-routing HA sans LLM -------------------------
    ha_verbs = LANG.ha_action_verbs
    protected = list(PERSONAS.family_names)

    import re as _re_state2
    is_state_phrase = bool(_re_state2.search(LANG.state_regex, lower))
    is_person   = any(n in lower for n in protected)
    has_verb    = any(v in lower for v in ha_verbs)

    if has_verb and not is_person and not is_state_phrase:
        print(f"  ⚡ Bypass LLM HA direct")
        reply_content, ha_ack = execute_category("HA", user_text, satellite)
        print(f"  ⏱ /chat total : {_time.time()-_t_start:.2f}s → HA")
        return _make_response(user_text, room, reply_content, ha_ack, "HA", False)

    # ------------------------ LLM -------------------------
    category = "SPEECH"
    reply_content = ""
    ha_ack = "no_action"
    expect_reply = False


    if USE_LLM == 1:
        try:
            category, reply_content, expect_reply = router_llm(user_text, satellite)
        except Exception as e:
            print(f"  ⚡ Error LLM /chat : {e}")

    if not reply_content:
        reply_content, ha_ack = execute_category(category, user_text, satellite)

    print(f"  ⏱ /chat total : {_time.time()-_t_start:.2f}s → {category}")
    return _make_response(user_text, room, reply_content, ha_ack, category, expect_reply, satellite=satellite)


def _make_response(heard, room, reply, ha_ack, category, expect_reply,
                   satellite: dict = None) -> dict:
    # Personalize with the first name if satellite provided and speaker identified
    if satellite:
        reply = personalize(reply, satellite)
    return {
        "status":        "success",
        "heard":         heard,
        "room":          room,
        "reply":         reply,
        "ha_ack":        ha_ack,
        "category":      category,
        "expect_reply":  expect_reply,
        "tts_available": tts_backend.backend_name() != "none",
    }


@app.post("/enroll")
async def enroll_speaker(request: Request):
    """
    Register or update a person's voice profile.
    Uses 10-30 seconds of WAV audio to create the pyannote embedding.

    Two modes:
      A) Multipart form: WAV file uploaded directly
      B) JSON body with audio in base64

    Examples:
      curl -X POST http://host:8000/enroll \
           -H "X-Token: ADMIN_TOKEN" \
           -F "name=Emmanuel" \
           -F "audio=@sample_emmanuel.wav"

      curl -X POST http://host:8000/enroll \
           -H "X-Token: ADMIN_TOKEN" \
           -H "Content-Type: application/json" \
           -d '{"name": "Emmanuel", "audio_b64": "<base64 wav>"}'
    """
    token = request.headers.get("X-Token", "")
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Admin token required")

    if not speaker_backend:
        raise HTTPException(status_code=503,
            detail="Speaker ID not available — pip install pyannote.audio + SPEAKER_ENABLED=1")

    content_type = request.headers.get("content-type", "")

    # Mode A : multipart form (WAV file)
    if "multipart" in content_type:
        from fastapi import Form, UploadFile, File
        form = await request.form()
        name  = str(form.get("name", "")).strip()
        audio = form.get("audio")
        if not name:
            raise HTTPException(status_code=400, detail="Missing 'name' field")
        if not audio:
            raise HTTPException(status_code=400, detail="Missing 'audio' field")
        wav_bytes = await audio.read()

    # Mode B : JSON with base64
    else:
        import base64
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Missing 'name' field")
        if "audio_b64" in body:
            wav_bytes = base64.b64decode(body["audio_b64"])
        else:
            raise HTTPException(status_code=400,
                detail="Provide 'audio_b64' (base64) or multipart with 'audio'")

    if len(wav_bytes) < 1000:
        raise HTTPException(status_code=400,
            detail="Audio too short — minimum 10 seconds recommended")

    result = speaker_backend.enroll(name, wav_bytes)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("detail", "Enrollment failed"))

    print(f"✅ Enrollment : {name} ({result.get('n_samples', 1)} sample(s))")
    return result


@app.get("/speakers")
async def list_speakers(request: Request):
    """List registered voice profiles."""
    token = request.headers.get("X-Token", "")
    if token != API_TOKEN:
        raise HTTPException(status_code=403)
    if not speaker_backend:
        return {"profiles": [], "speaker_enabled": False}
    return {
        "profiles": speaker_backend.list_profiles(),
        "speaker_enabled": KIRA.speaker_enabled,
    }


@app.delete("/speakers/{name}")
async def delete_speaker(name: str, request: Request):
    """Delete a voice profile."""
    token = request.headers.get("X-Token", "")
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Admin token required")
    if not speaker_backend:
        raise HTTPException(status_code=503)
    ok = speaker_backend.delete_profile(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    return {"status": "ok", "deleted": name}


@app.get("/health")
async def health():
    return {
        "status":     "ok",
        "satellites": len(SATELLITES_TABLE),
        "model_stt":  stt.backend_name(),
        "model_llm":  llm.backend_name(),
        "memory_facts":   len(memory.get_facts()),
        "memory_queries": sum(s["count"] for s in memory.get_stats(100)),
        "model_tts":  tts_backend.backend_name(),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
