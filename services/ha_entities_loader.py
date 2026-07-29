"""
services/ha_entities_loader.py — Load Home Assistant entities and aliases for Kira

Request API REST de Home Assistant pour récupérer toutes les entités
des domaines utiles à Kira, avec leurs friendly_name et aliases.

Replace ha_entities.json (maintained manually) with a truth source
synchronized with HA at each server startup.

Domains retrieved:
  light       → lamps, spots, LED strips
  switch      → outlets, switches
  cover       → shutters, blinds, garage doors
  climate     → thermostats, air conditioning
  media_player→ TV, speakers, set-top box
  input_boolean→ helpers on/off
  fan         → fans
  lock        → locks
  sensor      → sensors (temperature, humidity...)
  binary_sensor→ detectors, binary sensors

Usage in server.py :
    from services.ha_entities_loader import load_ha_entities
    HA_ENTITIES = load_ha_entities()
    # HA_ENTITIES is a list of strings: ["Living Room Lamp", "Bedroom Blind", ...]
"""

import os
import json
import requests
from dotenv import load_dotenv

try:
    from services.config_loader import LANG as _LANG, KIRA as _KIRA
except ImportError:
    _LANG = None
    _KIRA = None

load_dotenv()

# HA_URL_C = Clean URL without /api (for direct calls to /api/states)
HA_URL = os.getenv("HA_URL_C", os.getenv("HA_URL", "http://homeassistant.local:8123")).rstrip("/").removesuffix("/api")
HA_TOKEN = os.getenv("HA_TOKEN", "")
TIMEOUT  = 8

# Always expose a valid alias map. It is populated when a local Home Assistant
# entity registry is available and remains empty when the offline fallback is
# used.
HA_ALIAS_MAP: dict[str, str] = {}

# Domains to retrieve — covers everything Kira can control or query
DOMAINS = [
    "light",
    "switch",
    "cover",
    "climate",
    "media_player",
    "fan",
    "lock",
    "input_boolean",
    "sensor",
    "binary_sensor",
    "scene",
    "script",
    "automation",
]

# States to exclude — entities probably useless for Kira
EXCLUDED_STATES = {"unavailable", "unknown"}

# Entity_id prefixes to ignore (HA system entities)
EXCLUDED_PREFIXES = [
    "sensor.sun_",
    "sensor.time",
    "sensor.date",
    "binary_sensor.updater",
    "sensor.last_boot",
]

# Additional prefixes from kira.yaml → ha.excluded_prefixes
_extra_prefixes = (_KIRA.ha_excluded_prefixes if _KIRA else [])
EXCLUDED_PREFIXES = list(set(EXCLUDED_PREFIXES + _extra_prefixes))


def load_from_registry_file(
    file_path: str = "HA/core.entity_registry"
) -> tuple[list[str], dict[str, str]]:
    """
    Load entities that have aliases in HA Assist.

    For each entity with an alias, we index:
      - the friendly_name (real name in HA)
      - all aliases defined in Assist

    Returns:
        names     : list of all names + aliases (for Whisper hint)
        alias_map : dict {term_lower → entity_id} for direct resolution
    """
    import os
    if not os.path.exists(file_path):
        print(f"⚠️  {file_path} not found")
        return [], {}

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entities  = data.get("data", {}).get("entities", [])
    alias_map : dict[str, str] = {}
    count_entities = 0

    for entry in entities:
        entity_id = entry.get("entity_id", "")

        # Extract aliases
        a1 = entry.get("aliases", []) or []
        a2 = [a for a in (entry.get("aliases_v2") or []) if a is not None]

        aliases_clean = []
        for a in a1 + a2:
            if isinstance(a, str) and a.strip():
                aliases_clean.append(a.strip())
            elif isinstance(a, dict) and a.get("name"):
                aliases_clean.append(a["name"].strip())

        # Ignore entities without aliases
        if not aliases_clean:
            continue

        count_entities += 1

        # friendly_name → entity_id
        friendly = (entry.get("name") or entry.get("original_name") or "").strip()
        if friendly:
            alias_map[friendly.lower()] = entity_id

        # Each alias → entity_id
        for alias in set(aliases_clean):
            alias_map[alias.lower()] = entity_id

        # Readable log: friendly_name + its aliases
        print(f"  📦 {friendly or entity_id} ({entity_id})")
        for alias in sorted(set(aliases_clean)):
            print(f"     🎤 '{alias}'")

    names = sorted(set(alias_map.keys()))
    print(f"\n✅ {count_entities} entity(ies) with aliases → {len(alias_map)} terms indexed")
    return names, alias_map


def load_ha_entities(
    fallback_file:  str = "config/ha_entities.json",
    registry_file:  str = "HA/core.entity_registry",
) -> list[str]:
    """
    Load HA entities and aliases in this order of priority:
      1. core.entity_registry (local HA file — most complete)
      2. HA REST API /api/states (if registry absent)
      3. ha_entities.json (fallback offline)

    Also fills HA_ALIAS_MAP for direct alias → entity_id resolution.

    Returns:
        list[str] : all names and aliases for Whisper hint + fuzzy-match
    """
    global HA_ALIAS_MAP

    # ── Priority 1 : core.entity_registry ────────────────────────────────────
    names, alias_map = load_from_registry_file(registry_file)
    if names:
        HA_ALIAS_MAP = alias_map
        _save_cache(names, fallback_file)
        return names

    # ── Priority 2 : fallback static file ─────────────────────────────────
    # HA REST API does not return Assist aliases → registry file only
    print("⚠️  core.entity_registry absent — fallback ha_entities.json")
    return _load_fallback(fallback_file)


def get_alias_map() -> dict[str, str]:
    """
    Returns the dict alias_lower → entity_id.
    Used in normalize_for_ha for direct resolution without fuzzy-match.
    """
    return HA_ALIAS_MAP


def _fetch_from_ha() -> list[str]:
    """
    Calls GET /api/states and filters useful entities.
    Returns a list of names (friendly_name + aliases) deduplicated.
    """
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type":  "application/json",
    }

    r = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    all_states = r.json()

    names = set()
    stats = {}

    for state in all_states:
        entity_id = state.get("entity_id", "")
        domain    = entity_id.split(".")[0] if "." in entity_id else ""

        # Filter domains
        if domain not in DOMAINS:
            continue

        # Filter system prefixes
        if any(entity_id.startswith(p) for p in EXCLUDED_PREFIXES):
            continue

        # Filter unavailable states
        if state.get("state") in EXCLUDED_STATES:
            continue

        attrs = state.get("attributes", {})

        # friendly_name — this is what the user says aloud
        friendly = attrs.get("friendly_name", "").strip()
        if friendly and len(friendly) > 1:
            names.add(friendly)

        # HA aliases (defined in the HA UI → Settings → Devices)
        for alias in attrs.get("aliases", []):
            alias = alias.strip()
            if alias and len(alias) > 1:
                names.add(alias)

        # Count by domain for stats
        stats[domain] = stats.get(domain, 0) + 1

    # Log by domain
    for domain, count in sorted(stats.items()):
        print(f"   HA API : {count} {domain}(s)")

    return sorted(names)


def _load_fallback(file_path: str) -> list[str]:
    """Load ha_entities.json if available."""
    import json, os
    if not os.path.exists(file_path):
        print(f"   No fallback found: {file_path}")
        return []
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        entities = [e for e in data.get("entities", []) if not e.startswith("_")]
        print(f"   Fallback: {len(entities)} entity(ies) from {file_path}")
        return entities
    except Exception as e:
        print(f"   Fallback error: {e}")
        return []


def _save_cache(entities: list[str], file_path: str):
    """
    Save entities to ha_entities.json as an offline cache.
    If HA is unreachable on the next startup, this file is used.
    """
    import json, os
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    data = {
        "_comment": "Automatically generated from the HA API — do not edit manually",
        "_count":   len(entities),
        "entities": entities,
    }
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   Cache save error: {e}")


def get_entity_id_map() -> dict[str, str]:
    """
    Returns a dict friendly_name → entity_id for precise commands.
    Useful for constructing direct HA calls without going through /conversation/process.

    Returns:
        dict: {"Lampe Salon": "light.lampe_salon", "Volet Chambre": "cover.volet_chambre", ...}
    """
    if not HA_TOKEN or not HA_URL:
        return {}

    try:
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type":  "application/json",
        }
        r = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        all_states = r.json()

        mapping = {}
        for state in all_states:
            entity_id = state.get("entity_id", "")
            domain    = entity_id.split(".")[0] if "." in entity_id else ""
            if domain not in DOMAINS:
                continue
            attrs   = state.get("attributes", {})
            friendly = attrs.get("friendly_name", "").strip()
            if friendly:
                mapping[friendly.lower()] = entity_id
            for alias in attrs.get("aliases", []):
                if alias.strip():
                    mapping[alias.strip().lower()] = entity_id

        return mapping

    except Exception as e:
        print(f"⚠️  entity_id_map erreur : {e}")
        return {}


def get_room_entity_map() -> dict[str, list[dict]]:
    """
    Returns a dict room → list of entities for that room.
    Based on the friendly_name which often contains the room name.

    Returns:
        dict: {
            "salon":   [{"name": "Lampe Salon", "entity_id": "light.lampe_salon", "domain": "light"}],
            "entrée":  [{"name": "Lampe Entrée", "entity_id": "light.lampe_entree", "domain": "light"}],
            ...
        }
    """
    if not HA_TOKEN or not HA_URL:
        return {}

    # Room keywords from config/lang/<lang>.yaml → room_keywords
    ROOM_KEYWORDS = (
        _LANG._d.get("room_keywords", {})
        if _LANG and hasattr(_LANG, "_d")
        else {}
    )
    if not ROOM_KEYWORDS:
        print("⚠️  room_keywords absent de lang.yaml — room-entity map désactivée")
        return {}

    try:
        headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        r = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        all_states = r.json()
    except Exception as e:
        print(f"⚠️  room_entity_map erreur : {e}")
        return {}

    room_map: dict[str, list[dict]] = {}

    for state in all_states:
        entity_id = state.get("entity_id", "")
        domain    = entity_id.split(".")[0] if "." in entity_id else ""
        if domain not in ["light", "switch", "cover", "climate", "media_player", "fan"]:
            continue
        if state.get("state") in EXCLUDED_STATES:
            continue

        attrs    = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").strip().lower()
        if not friendly:
            continue

        # Look for which room is mentioned in the friendly_name
        for room, keywords in ROOM_KEYWORDS.items():
            if any(kw in friendly for kw in keywords):
                if room not in room_map:
                    room_map[room] = []
                room_map[room].append({
                    "name":      attrs.get("friendly_name", "").strip(),
                    "entity_id": entity_id,
                    "domain":    domain,
                })
                break  # an entity → a piece

    total = sum(len(v) for v in room_map.values())
    print(f"✅ Room-entity map : {total} entité(s) dans {len(room_map)} pièce(s)")
    for room, entities in sorted(room_map.items()):
        names = ", ".join(e["name"] for e in entities[:3])
        print(f"   {room} : {names}{'...' if len(entities) > 3 else ''}")

    return room_map


# ── Command line test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Loading HA entities...")
    entities = load_ha_entities()
    print(f"\n{len(entities)} entity(ies) :")
    for e in entities:
        print(f"  - {e}")

    print("\nMapping entity_id :")
    mapping = get_entity_id_map()
    for name, eid in list(mapping.items())[:10]:
        print(f"  {name} → {eid}")
