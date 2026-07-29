"""
services/config_loader.py — Centralized loading of YAML configuration files

Loads all YAML files from config/ at startup and exposes
global variables used by server.py, get_ha_state.py, etc.

Usage :
    from services.config_loader import LANG, PERSONAS, PHONETIC, ROOM_GROUPS

Architecture :
    config/
    ├── lang/fr.yaml       ← Keywords, responses, HA states (according to LANGUAGE=fr)
    ├── personas.yaml      ← family names, wake word variants
    ├── phonetic.yaml      ← Whisper phonetic corrections
    └── room_groups.yaml   ← room groups
"""

import os
import re
from typing import Any

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False
    print("⚠️  PyYAML not installed — pip install pyyaml")
    print("   YAML configs will be ignored, default values will be used")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_yaml(path: str) -> dict:
    """Load a YAML file. Returns {} if missing or error."""
    if not _YAML_AVAILABLE:
        return {}
    if not os.path.exists(path):
        print(f"⚠️  Configuration not available : {path}")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except Exception as e:
        print(f"⚠️  Error reading {path} : {e}")
        return {}


def _get(data: dict, *keys, default=None) -> Any:
    """Safe access in a nested dict."""
    for key in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(key, default)
        if data is None:
            return default
    return data


# ── Chargement ─────────────────────────────────────────────────────────────────

# kira.yaml loaded first — determines language and other parameters
_raw_kira = _load_yaml("config/kira.yaml")

# Language is determined by: .env LANGUAGE > kira.yaml language > "fr"
_LANG = os.getenv("LANGUAGE",
        _raw_kira.get("language", "fr")).lower()

_raw_lang     = _load_yaml(f"config/lang/{_LANG}.yaml")
_raw_personas = _load_yaml("config/personas.yaml")
_raw_phonetic = _load_yaml("config/phonetic.yaml")
_raw_groups   = _load_yaml("config/room_groups.yaml")

# Fallback to French if language not found
if not _raw_lang and _LANG != "fr":
    print(f"⚠️  config/lang/{_LANG}.yaml absent — fallback fr")
    _raw_lang = _load_yaml("config/lang/fr.yaml")

if _raw_lang:
    print(f"✅ Language loaded : {_raw_lang.get('language_name', _LANG)}")
if _raw_personas:
    print(f"✅ Personas loaded")
if _raw_phonetic:
    print(f"✅ Phonetic corrections : {len(_raw_phonetic.get('corrections', {}))} entries")
if _raw_groups:
    print(f"✅ Room groups : {len(_raw_groups.get('groups', {}))} entries")


# ══════════════════════════════════════════════════════════════════════════════
# LANGUAGE — keywords and responses
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# KIRA CONFIG — general parameters
# ══════════════════════════════════════════════════════════════════════════════

class KiraConfig:
    """Typed access to config/kira.yaml parameters."""

    def __init__(self, data: dict):
        self._d = data

    def _get(self, *keys, default=None):
        return _get(self._d, *keys, default=default)

    # ── Langue ────────────────────────────────────────────────────────────────
    @property
    def language(self) -> str:
        return self._d.get("language", "fr")

    # ── LLM ───────────────────────────────────────────────────────────────────
    @property
    def llm_enabled(self) -> bool:
        return self._get("llm", "enabled", default=True)

    @property
    def llm_model(self) -> str:
        return self._get("llm", "model",
               default=os.getenv("LLM_MODEL", "qwen2.5:3b"))

    @property
    def llm_temperature(self) -> float:
        return float(self._get("llm", "temperature",
               default=os.getenv("LLM_TEMPERATURE", 0.7)))

    @property
    def llm_num_predict(self) -> int:
        return int(self._get("llm", "num_predict",
               default=os.getenv("LLM_NUM_PREDICT", 50)))

    # ── TTS ───────────────────────────────────────────────────────────────────
    @property
    def tts_cache_enabled(self) -> bool:
        return self._get("tts", "cache_enabled", default=True)

    @property
    def tts_cache_db(self) -> str:
        return self._get("tts", "cache_db", default="data/tts_cache.db")

    # ── Speaker ───────────────────────────────────────────────────────────────
    @property
    def speaker_enabled(self) -> bool:
        env = os.getenv("SPEAKER_ENABLED")
        if env is not None:
            return env == "1"
        return self._get("speaker", "enabled", default=False)

    @property
    def speaker_threshold(self) -> float:
        env = os.getenv("SPEAKER_THRESHOLD")
        if env is not None:
            return float(env)
        return float(self._get("speaker", "threshold", default=0.45))

    # ── Météo ─────────────────────────────────────────────────────────────────
    @property
    def weather_default_city(self) -> str:
        return self._get("weather", "default_city", default="Genève")

    @property
    def weather_cache_minutes(self) -> int:
        return int(self._get("weather", "cache_minutes", default=15))

    # ── Mémoire ───────────────────────────────────────────────────────────────
    @property
    def memory_db(self) -> str:
        return os.getenv("MEMORY_DB",
               self._get("memory", "db", default="data/kira_memory.db"))

    @property
    def memory_max_history(self) -> int:
        return int(self._get("memory", "max_history", default=20))

    # ── Serveur ───────────────────────────────────────────────────────────────
    @property
    def server_port(self) -> int:
        return int(self._get("server", "port", default=8000))

    @property
    def save_audio(self) -> bool:
        return self._get("server", "save_audio", default=False)

    @property
    def save_audio_dir(self) -> str:
        return self._get("server", "save_audio_dir", default="/tmp/kira_audio")

    # ── HA ────────────────────────────────────────────────────────────────────
    @property
    def ha_registry_file(self) -> str:
        return self._get("ha", "registry_file", default="HA/core.entity_registry")

    @property
    def ha_excluded_prefixes(self) -> list[str]:
        return self._get("ha", "excluded_prefixes", default=[
            "switch.switch", "switch.poe", "switch.port",
            "switch.router", "switch.hub", "switch.nas",
        ])

    @property
    def ha_excluded_keywords(self) -> list[str]:
        return self._get("ha", "excluded_keywords", default=[
            "camera", "sensor", "motion", "alarm", "siren",
            "temperature", "lora", "humidity", "co2",
        ])

    @property
    def ha_timeout(self) -> int:
        return int(self._get("bypass", "ha_timeout", default=5))

    # ── Whisper ───────────────────────────────────────────────────────────────
    @property
    def whisper_max_alias(self) -> int:
        return int(self._get("whisper", "max_alias_in_hint", default=20))

    @property
    def whisper_max_entities(self) -> int:
        return int(self._get("whisper", "max_entities_in_hint", default=10))

    @property
    def phonetic_enabled(self) -> bool:
        return self._get("whisper", "phonetic_correction", default=True)

    # ── Proactif ──────────────────────────────────────────────────────────────
    @property
    def proactive_enabled(self) -> bool:
        env = os.getenv("PROACTIVE_ENABLED")
        if env is not None:
            return env == "1"
        return self._get("proactive", "enabled", default=False)

    @property
    def proactive_timezone(self) -> str:
        return os.getenv("PROACTIVE_TZ",
               self._get("proactive", "timezone", default="Europe/Paris"))


class LangConfig:
    """Typed access to language configuration."""

    def __init__(self, data: dict):
        self._d = data

    @property
    def language(self) -> str:
        return self._d.get("language", "fr")

    @property
    def whisper_hint_base(self) -> str:
        return self._d.get("whisper_hint_base",
            "allume, éteins, ouvre, ferme, coupe, lumière, salon")

    @property
    def ha_action_verbs(self) -> list[str]:
        return self._d.get("ha_action_verbs", ["allume", "éteins", "coupe"])

    @property
    def time_keywords(self) -> list[str]:
        return self._d.get("time_keywords", ["quelle heure"])

    @property
    def time_hour_keywords(self) -> list[str]:
        return self._d.get("time_hour_keywords", ["heure"])

    @property
    def time_date_keywords(self) -> list[str]:
        return self._d.get("time_date_keywords", ["date", "jour"])

    @property
    def weather_keywords(self) -> list[str]:
        return self._d.get("weather_keywords", ["météo"])

    @property
    def weather_tomorrow(self) -> str:
        return self._d.get("weather_tomorrow", "demain")

    @property
    def weather_day_after(self) -> str:
        return self._d.get("weather_day_after", "après-demain")

    @property
    def weather_cities(self) -> list[str]:
        return self._d.get("weather_cities", ["genève"])

    @property
    def state_question_keywords(self) -> list[str]:
        return self._d.get("state_question_keywords", [
            "est allumée", "est éteinte", "est ouvert", "est fermé"
        ])

    @property
    def sensor_question_keywords(self) -> list[str]:
        return self._d.get("sensor_question_keywords", ["température"])

    @property
    def sensor_question_markers(self) -> list[str]:
        return self._d.get("sensor_question_markers", ["quelle", "?"])

    @property
    def question_markers(self) -> list[str]:
        return self._d.get("question_markers", ["?", "est-ce que", "j'ai"])

    @property
    def state_regex(self) -> str:
        patterns = self._d.get("state_regex_patterns", [])
        if not patterns:
            return r"est[- ]ce que"
        return "(" + "|".join(patterns) + ")"

    @property
    def synonyms(self) -> dict[str, str]:
        return self._d.get("synonyms", {"lumière": "lampe"})

    @property
    def articles(self) -> list[str]:
        return self._d.get("articles", ["le ", "la ", "les ", "l'"])

    @property
    def weekdays(self) -> list[str]:
        return _get(self._d, "responses", "weekdays", default=[
            "lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"
        ])

    @property
    def months(self) -> list[str]:
        return _get(self._d, "responses", "months", default=[
            "janvier","février","mars","avril","mai","juin",
            "juillet","août","septembre","octobre","novembre","décembre"
        ])

    @property
    def time_format(self) -> str:
        return _get(self._d, "responses", "time_format",
                    default="Il est {hour} heures {minute}.")

    @property
    def date_format(self) -> str:
        return _get(self._d, "responses", "date_format",
                    default="Nous sommes le {weekday} {day} {month} {year}.")

    @property
    def datetime_format(self) -> str:
        return _get(self._d, "responses", "datetime_format",
                    default="Il est {hour} heures {minute}, le {weekday} {day} {month} {year}.")

    # ── Heure formatée ────────────────────────────────────────────────────────

    def time_reply(self, h: int, m: int) -> str:
        """Génère la réponse heure naturelle selon la langue."""
        singular = self._d.get("time_hour_singular", "heure")
        plural   = self._d.get("time_hour_plural",   "heures")
        hu = singular if h == 1 else plural
        if m == 0:
            tpl = self._d.get("time_oclock",      "Il est {h} {hu}.")
        elif m == 15:
            tpl = self._d.get("time_quarter_past", "{h} {hu} et quart.")
        elif m == 30:
            tpl = self._d.get("time_half",         "{h} {hu} et demie.")
        elif m == 45:
            tpl = self._d.get("time_quarter_to",   "{h} {hu} moins le quart.")
        else:
            tpl = self._d.get("time_exact",        "Il est {h} {hu} {m}.")
        return tpl.format(h=h, hu=hu, m=str(m).zfill(2))

    # ── Groupe pièces ─────────────────────────────────────────────────────────

    @property
    def group_lights_on(self) -> str:
        return self._d.get("group_lights_on", "Les lampes de {room} sont allumées.")

    @property
    def group_lights_off(self) -> str:
        return self._d.get("group_lights_off", "Les lampes de {room} sont éteintes.")

    # ── Messages système ──────────────────────────────────────────────────────

    @property
    def tool_unknown(self) -> str:
        return self._d.get("tool_unknown",
            "J'ai essayé d'utiliser un outil que je ne maîtrise pas encore.")

    @property
    def tool_no_result(self) -> str:
        return self._d.get("tool_no_result", "Je n'ai pas pu obtenir l'information.")

    @property
    def idle_response(self) -> str:
        return self._d.get("idle_response", "Je vous écoute.")

    @property
    def tool_reformulation(self) -> str:
        return self._d.get("tool_reformulation",
            "Tu es Kira. Réponds UNIQUEMENT en {language_name}. "
            "Reformule le résultat en une phrase naturelle courte. "
            "Pas de listes, pas de markdown, pas de symboles.")

    # ── Speaker + questions personnelles ──────────────────────────────────────

    @property
    def speaker_context(self) -> str:
        return self._d.get("speaker_context",
            "\nL'utilisateur s'appelle {name} (confiance: {conf}). Utilise son prénom naturellement.\n")

    @property
    def person_question_patterns(self) -> list[str]:
        return self._d.get("person_question_patterns", [
            "qui est", "qu'est", "c'est qui", "connais-tu", "sais-tu"
        ])

    # ── detect_expect_reply ───────────────────────────────────────────────────

    @property
    def no_reply_patterns(self) -> list[str]:
        return self._d.get("no_reply_patterns", [
            "je n'ai pas reçu","je n'ai pas compris","je ne sais pas",
            "veuillez préciser","quel appareil","quelle pièce","plusieurs appareils",
        ])

    @property
    def question_regex_patterns(self) -> list[str]:
        return self._d.get("question_regex_patterns", [
            r"\blequel\b", r"\blaquelle\b", r"\bquel\b", r"\bquelle\b",
            r"\bvoulez-vous\b", r"\bsouhaitez-vous\b",
            r"\bpuis-je\b", r"\bpouvez-vous préciser\b",
        ])

    # ── Langue ────────────────────────────────────────────────────────────────

    @property
    def language_name(self) -> str:
        return self._d.get("language_name", "français")

    # ── États HA ──────────────────────────────────────────────────────────────

    @property
    def ha_states(self) -> dict:
        return self._d.get("ha_states", {})

    def ha_state(self, key: str, **kwargs) -> str:
        template = self.ha_states.get(key, key)
        try:
            return template.format(**kwargs)
        except KeyError:
            return template

    # ── Personnalisation ──────────────────────────────────────────────────────

    @property
    def short_threshold(self) -> int:
        return _get(self._d, "personalization", "short_threshold", default=40)

    def personalize(self, reply: str, name: str) -> str:
        if not name or not reply or name.lower() in reply.lower():
            return reply
        cfg = self._d.get("personalization", {})
        if len(reply) < self.short_threshold:
            tpl = cfg.get("short_reply", "{reply}, {name}.")
            return tpl.format(reply=reply.rstrip(".!"), name=name)
        else:
            tpl = cfg.get("long_reply", "{name}, {reply_lower}")
            return tpl.format(name=name, reply_lower=reply[0].lower() + reply[1:])

    def normalize_text(self, text: str) -> str:
        lower = text.lower()
        for src, dst in self.synonyms.items():
            lower = lower.replace(src, dst)
        article_pattern = "|".join(re.escape(a) for a in self.articles)
        lower = re.sub(rf"\b({article_pattern})", "", lower).strip()
        return lower


# ══════════════════════════════════════════════════════════════════════════════
# PERSONAS — famille et variantes du nom
# ══════════════════════════════════════════════════════════════════════════════

class PersonasConfig:
    def __init__(self, data: dict):
        self._d = data

    @property
    def family_names(self) -> list[str]:
        return _get(self._d, "family", "names", default=[
            "colussi", "véronique", "emmanuel"
        ])

    @property
    def personal_questions(self) -> list[str]:
        return _get(self._d, "family", "personal_questions", default=[
            "qui est", "qu'est", "c'est qui"
        ])

    @property
    def assistant_variants(self) -> list[str]:
        return self._d.get("assistant_name_variants", [
            "kira", "kyra", "tyra", "tira"
        ])


# ══════════════════════════════════════════════════════════════════════════════
# PHONÉTIQUE — corrections Whisper
# ══════════════════════════════════════════════════════════════════════════════

class PhoneticConfig:
    def __init__(self, data: dict):
        self._d = data

    @property
    def corrections(self) -> dict[str, str]:
        return self._d.get("corrections", {})

    @property
    def force_vocabulary(self) -> list[str]:
        return self._d.get("force_vocabulary", [])

    def apply(self, text: str) -> str:
        """Applique toutes les corrections phonétiques sur un texte."""
        lower = text.lower()
        for wrong, correct in self.corrections.items():
            if wrong in lower:
                lower = lower.replace(wrong, correct)
        # Restaurer la casse du premier caractère
        if text and text[0].isupper() and lower:
            lower = lower[0].upper() + lower[1:]
        return lower


# ══════════════════════════════════════════════════════════════════════════════
# GROUPS OF ROOMS
# ══════════════════════════════════════════════════════════════════════════════

class RoomGroupsConfig:
    def __init__(self, data: dict):
        self._d = data

    @property
    def groups(self) -> dict[str, Any]:
        """Dict alias → entity_id (str) ou [entity_id, ...] (list)."""
        return self._d.get("groups", {})

    @property
    def specific_keywords(self) -> list[str]:
        """Mots indiquant une lampe spécifique → pas de bypass groupe."""
        return self._d.get("specific_lamp_keywords", [
            "droite", "gauche", "table", "panneau"
        ])

    @property
    def action_verbs(self) -> list[str]:
        return self._d.get("group_action_verbs", ["allume", "éteins", "coupe"])

    def get_entities(self, alias: str) -> list[str]:
        """Retourne la liste d'entity_id pour un alias de groupe."""
        val = self.groups.get(alias.lower())
        if val is None:
            return []
        if isinstance(val, list):
            return val
        return [val]

    def find_group(self, text_lower: str) -> tuple[str, list[str]]:
        """
        Trouve le groupe correspondant dans le texte.
        Retourne (alias_trouvé, [entity_ids]) ou ("", []).
        Trie par longueur décroissante pour préférer l'alias le plus précis.
        """
        # Ne pas déclencher si lampe spécifique mentionnée
        if any(kw in text_lower for kw in self.specific_keywords):
            return "", []

        for alias in sorted(self.groups.keys(), key=len, reverse=True):
            if alias in text_lower:
                return alias, self.get_entities(alias)
        return "", []


# ══════════════════════════════════════════════════════════════════════════════
# INSTANCES GLOBALES
# ══════════════════════════════════════════════════════════════════════════════

KIRA        = KiraConfig(_raw_kira)
LANG        = LangConfig(_raw_lang)
PERSONAS    = PersonasConfig(_raw_personas)
PHONETIC    = PhoneticConfig(_raw_phonetic)
ROOM_GROUPS = RoomGroupsConfig(_raw_groups)

# ── Test en ligne de commande ──────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n=== Config chargée ===")
    print(f"Kira config : LLM={KIRA.llm_model}, speaker={KIRA.speaker_enabled}, seuil={KIRA.speaker_threshold}")
    print(f"Langue      : {LANG.language}")
    print(f"Verbes HA   : {LANG.ha_action_verbs[:5]}...")
    print(f"Famille     : {PERSONAS.family_names}")
    print(f"Corrections : {list(PHONETIC.corrections.items())[:3]}...")
    print(f"Groupes     : {list(ROOM_GROUPS.groups.keys())[:5]}...")

    print(f"\n=== Tests ===")
    print(f"Phonétique  : 'et un salon' → '{PHONETIC.apply('et un salon')}'")
    print(f"Normalize   : 'la lumière salon' → '{LANG.normalize_text('la lumière salon')}'")
    print(f"Personalize : 'Il est 14h.' → '{LANG.personalize('Il est 14h.', 'Emmanuel')}'")
    print(f"Groupe      : 'petit salon' → {ROOM_GROUPS.get_entities('petit salon')}")
