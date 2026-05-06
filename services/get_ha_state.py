"""
services/get_ha_state.py — Read the state of a Home Assistant entity

Allows Kira to answer questions about the state of the home:
  "What is the outside temperature?"
  "Is the living room light on?"
  "What is the humidity in the bedroom?"

All response strings come from config/lang/<lang>.yaml via LANG.
No hardcoded language-specific text in this file.

Uses the HA REST API: GET /api/states/<entity_id>
"""

import os
import requests
from dotenv import load_dotenv
from difflib import get_close_matches

try:
    from services.config_loader import LANG as _LANG
except ImportError:
    _LANG = None

load_dotenv()

HA_URL   = os.getenv("HA_URL_C", os.getenv("HA_URL", "http://homeassistant.local:8123")).rstrip("/").removesuffix("/api")
HA_TOKEN = os.getenv("HA_TOKEN", "")
TIMEOUT  = 5


# ── Language helpers ──────────────────────────────────────────────────────────

def _t(key: str, **kwargs) -> str:
    """
    Returns a translated string from LANG config.
    Falls back to the key itself if LANG unavailable or key missing.
    """
    if _LANG and hasattr(_LANG, "_d"):
        tpl = _LANG._d.get(key, "")
        if tpl:
            try:
                return tpl.format(**kwargs)
            except KeyError:
                return tpl
    return key


def _unit_label(unit: str) -> str:
    """Returns the spoken label for a HA unit of measurement."""
    if _LANG and hasattr(_LANG, "_d"):
        labels = _LANG._d.get("unit_labels", {})
        return labels.get(unit, unit)
    return unit


def _binary_state_label(state: str) -> str:
    """Returns the spoken label for a binary state value."""
    if _LANG and hasattr(_LANG, "_d"):
        labels = _LANG._d.get("binary_state_labels", {})
        return labels.get(state.lower(), state)
    return state


# ── Public interface ──────────────────────────────────────────────────────────

def run(entity_id: str) -> str:
    """
    Reads the state of a HA entity and returns a natural language sentence.

    Args:
        entity_id : HA identifier (e.g. "sensor.temperature_exterieure")
                    Can be an approximate name — fuzzy match enabled.

    Returns:
        str : sentence ready for TTS in the configured language
    """
    if not HA_TOKEN or not HA_URL:
        return _t("ha_unavailable")

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type":  "application/json",
    }

    # Direct attempt
    entity = _fetch_state(entity_id, headers)

    # Fuzzy match if entity not found
    if entity is None:
        entity_id_matched = _fuzzy_find_entity(entity_id, headers)
        if entity_id_matched:
            entity = _fetch_state(entity_id_matched, headers)
            if entity:
                entity_id = entity_id_matched

    if entity is None:
        return _t("ha_entity_not_found", entity_id=entity_id)

    return _format_state(entity)


# ── API calls ─────────────────────────────────────────────────────────────────

def _fetch_state(entity_id: str, headers: dict) -> dict | None:
    """Calls GET /api/states/<entity_id>. Returns dict or None."""
    try:
        r = requests.get(
            f"{HA_URL}/api/states/{entity_id}",
            headers=headers,
            timeout=TIMEOUT
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _fuzzy_find_entity(name: str, headers: dict) -> str | None:
    """
    Lists all HA entities and finds the closest match to the given name.
    Useful when the LLM passes "temperature_exterieure" instead of
    "sensor.temperature_exterieure".
    """
    try:
        r = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        all_states = r.json()
    except Exception:
        return None

    entity_ids     = [s["entity_id"] for s in all_states]
    friendly_names = {
        s["entity_id"]: s.get("attributes", {}).get("friendly_name", "")
        for s in all_states
    }

    name_lower = name.lower().replace(" ", "_")

    matches = get_close_matches(name_lower, entity_ids, n=1, cutoff=0.5)
    if matches:
        return matches[0]

    fname_index = {fn.lower(): eid for eid, fn in friendly_names.items() if fn}
    matches = get_close_matches(name_lower, fname_index.keys(), n=1, cutoff=0.5)
    if matches:
        return fname_index[matches[0]]

    return None


# ── State formatting ──────────────────────────────────────────────────────────

def _format_state(entity: dict) -> str:
    """
    Converts a HA state dict into a natural language sentence.
    All strings come from LANG config (multilingual).
    """
    state     = entity.get("state", "")
    attrs     = entity.get("attributes", {})
    entity_id = entity.get("entity_id", "")
    domain    = entity_id.split(".")[0] if "." in entity_id else ""
    name      = attrs.get("friendly_name", entity_id.replace("_", " "))
    unit      = attrs.get("unit_of_measurement", "")

    # ── Numeric sensor (temperature, humidity, CO2...) ────────────────────────
    if domain == "sensor":
        try:
            value = float(state)
            unit_label = _unit_label(unit)
            if unit == "°C":
                return _t("sensor_celsius_reply", name=name, value=f"{value:.1f}")
            elif unit == "%":
                return _t("sensor_percent_reply", name=name, value=f"{value:.0f}")
            else:
                return _t("sensor_generic_reply", name=name, value=value, unit=unit_label)
        except ValueError:
            if state in ("unavailable", "unknown"):
                return _t("sensor_unavailable_reply", name=name)
            return _t("sensor_text_reply", name=name, state=state)

    # ── Lights ────────────────────────────────────────────────────────────────
    if domain == "light":
        brightness = attrs.get("brightness")
        if brightness and state == "on":
            pct = int(brightness / 255 * 100)
            return _t("light_brightness_reply", name=name, pct=pct)
        if state == "on":
            return _t("light_on_reply", name=name)
        return _t("light_off_reply", name=name)

    # ── Switches / inputs ─────────────────────────────────────────────────────
    if domain in ("switch", "input_boolean"):
        name_lower = name.lower()
        lamp_kw = (_LANG._d.get("lamp_keywords", []) if _LANG and hasattr(_LANG, "_d")
                   else ["lamp", "light", "led"])
        is_lamp = any(w in name_lower for w in lamp_kw)
        if is_lamp:
            return _t("switch_lamp_on" if state == "on" else "switch_lamp_off", name=name)
        return _t("switch_on" if state == "on" else "switch_off", name=name)

    # ── Covers (shutters, blinds) ─────────────────────────────────────────────
    if domain == "cover":
        if state == "open":
            pos = attrs.get("current_position")
            if pos is not None:
                return _t("cover_open_pct", name=name, pos=pos)
            return _t("cover_open", name=name)
        if state == "closed":
            return _t("cover_closed", name=name)
        if state == "opening":
            return _t("cover_opening", name=name)
        if state == "closing":
            return _t("cover_closing", name=name)
        return _t("cover_state", name=name, state=state)

    # ── Climate / thermostat ──────────────────────────────────────────────────
    if domain == "climate":
        current = attrs.get("current_temperature")
        target  = attrs.get("temperature")
        parts   = []
        if current is not None:
            parts.append(_t("climate_current_temp", value=current))
        if target is not None:
            parts.append(_t("climate_target_temp", value=target))
        if parts:
            return _t("climate_reply", name=name, parts=", ".join(parts))
        return _t("climate_mode", name=name, mode=state)

    # ── Local weather station ─────────────────────────────────────────────────
    if domain == "weather":
        temp = attrs.get("temperature")
        if temp is not None:
            return _t("weather_local_reply", temp=temp, cond=state)
        return _t("weather_local_cond", cond=state)

    # ── Binary sensors ────────────────────────────────────────────────────────
    if domain == "binary_sensor":
        device_class = attrs.get("device_class", "")
        state_lower  = state.lower()
        name_lower_bs = name.lower()

        # Mailbox / parcel detection
        mailbox_kw = (_LANG._d.get("mailbox_keywords", []) if _LANG and hasattr(_LANG, "_d")
                      else ["mail", "mailbox", "parcel"])
        is_mailbox = any(kw in name_lower_bs or kw in entity_id.lower()
                         for kw in mailbox_kw)
        if is_mailbox:
            is_detected = state_lower in ("on", "true", "detected", "1")
            parcel_kw = (_LANG._d.get("parcel_keywords", []) if _LANG and hasattr(_LANG, "_d")
                         else ["parcel"])
            if any(kw in name_lower_bs or kw in entity_id.lower()
                   for kw in parcel_kw):
                key = "parcel_detected" if is_detected else "parcel_empty"
            else:
                key = "mail_detected" if is_detected else "mail_empty"
            result = _t(key)
            return result if result != key else state

        # Connectivity: gateway, LoRa
        is_conn = (
            device_class == "connectivity"
            or any(kw in entity_id.lower()
                   for kw in ["gateway", "lora", "router", "network", "status"])
        )
        if is_conn:
            if state_lower in ("on", "online", "connected", "true"):
                return _t("connectivity_on", name=name)
            return _t("connectivity_off", name=name)

        # Presence
        if device_class in ("presence", "occupancy"):
            return _t("presence_on" if state_lower == "on" else "presence_off", name=name)

        # Door / window / garage
        if device_class in ("window", "door", "garage_door"):
            return _t("opening_open" if state_lower == "on" else "opening_closed", name=name)

        # Motion
        if device_class == "motion":
            return _t("motion_on" if state_lower == "on" else "motion_off", name=name)

        state_label = _binary_state_label(state_lower)
        return _t("binary_generic_reply", name=name, state=state_label)

    # ── Fallback ──────────────────────────────────────────────────────────────
    state_label = _binary_state_label(state.lower())
    return _t("fallback_reply", name=name, state=state_label)


# ── Command-line test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    entity = sys.argv[1] if len(sys.argv) > 1 else "weather.home"
    print(f"Entity : {entity}")
    print(f"Result : {run(entity)}")
