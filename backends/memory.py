"""
backends/memory.py — SQLite long-term memory for Kira

Stores two types of data:
  - facts   : persistent information about people and the house
               e.g., "Emmanuel likes dim lighting in the evening"
  - history : the last N exchanges per satellite (complements the RAM deque)

Memory is injected into the system prompt at each LLM call.

Usage in server.py :
    from backends import memory
    memory.init()
    facts = memory.get_facts()          # list of strings
    memory.save_fact("Emmanuel prefers 19°C at night")
    memory.save_exchange(sat_id, user_text, reply)
"""

import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

def _load_memory_config() -> dict:
    import yaml

    path = "config/kira.yaml"

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing required config file: {path}"
        )

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if "memory" not in cfg:
        raise KeyError("Missing required section: memory")

    memory = cfg["memory"]

    return {
        "db": memory["db"],
        "max_history": int(memory["max_history"]),
        "adaptive_threshold": int(memory["adaptive_threshold"]),
    }


_cfg = _load_memory_config()

DB_PATH           = _cfg["db"]
MAX_HISTORY       = _cfg["max_history"]
ADAPTIVE_THRESHOLD = _cfg["adaptive_threshold"]

_conn: sqlite3.Connection | None = None

_lang = None

def _get_lang():
    """Returns the LANG instance from config_loader (lazy loading)."""
    global _lang
    if _lang is None:
        try:
            from services.config_loader import LANG as _L
            _lang = _L
        except ImportError:
            _lang = None
    return _lang




# ───------------------------- Initialization -------------------------

def init():
    """Opens (or creates) the SQLite database. Called once at server startup."""
    global _conn
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row

    _conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            content   TEXT    NOT NULL,
            source    TEXT    DEFAULT 'user',
            created   TEXT    DEFAULT (datetime('now')),
            updated   TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            satellite_id INTEGER NOT NULL,
            role         TEXT    NOT NULL,
            content      TEXT    NOT NULL,
            created      TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS query_stats (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            intent       TEXT    NOT NULL,
            canonical    TEXT    NOT NULL,
            count        INTEGER DEFAULT 1,
            last_seen    TEXT    DEFAULT (datetime('now')),
            extra        TEXT    DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_history_sat ON history(satellite_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_stats_intent ON query_stats(intent, count DESC);
    """)
    _conn.commit()
    print(f"✅ SQLite memory initialized : {DB_PATH}")


# ──------------------------- Facts (semantic memory) ──-------------------------

def get_facts() -> list[str]:
    """Returns all known facts, from most recent to oldest."""
    if not _conn:
        return []
    rows = _conn.execute(
        "SELECT content FROM facts ORDER BY updated DESC"
    ).fetchall()
    return [r["content"] for r in rows]


def save_fact(content: str, source: str = "user"):
    """
    Saves a fact.
    If a similar fact already exists (same beginning of the sentence), updates it.
    """
    if not _conn or not content.strip():
        return

    # Look for an existing fact with the same first 30 chars
    key = content.strip()[:30].lower()
    existing = _conn.execute(
        "SELECT id FROM facts WHERE lower(substr(content,1,30)) = ?", (key,)
    ).fetchone()

    if existing:
        _conn.execute(
            "UPDATE facts SET content=?, updated=datetime('now') WHERE id=?",
            (content.strip(), existing["id"])
        )
    else:
        _conn.execute(
            "INSERT INTO facts (content, source) VALUES (?, ?)",
            (content.strip(), source)
        )
    _conn.commit()
    print(f"💾 Mémoire : '{content.strip()[:60]}'")


def delete_fact(fact_id: int):
    """Deletes a fact by its ID."""
    if _conn:
        _conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        _conn.commit()


def list_facts_with_ids() -> list[dict]:
    """For the admin endpoint /memory/facts."""
    if not _conn:
        return []
    rows = _conn.execute(
        "SELECT id, content, source, created FROM facts ORDER BY updated DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ──------------------------- History (persistent episodic memory) ──-------------------------

def save_exchange(satellite_id: int, user_text: str, reply: str):
    """Saves an exchange in the database. Purges old ones beyond MAX_HISTORY."""
    if not _conn:
        return

    now = datetime.now().isoformat()
    _conn.execute(
        "INSERT INTO history (satellite_id, role, content, created) VALUES (?,?,?,?)",
        (satellite_id, "user", user_text, now)
    )
    _conn.execute(
        "INSERT INTO history (satellite_id, role, content, created) VALUES (?,?,?,?)",
        (satellite_id, "assistant", reply, now)
    )

    # Keep only the last MAX_HISTORY exchanges (2 rows = 1 exchange)
    _conn.execute("""
        DELETE FROM history
        WHERE satellite_id = ?
          AND id NOT IN (
            SELECT id FROM history
            WHERE satellite_id = ?
            ORDER BY id DESC
            LIMIT ?
          )
    """, (satellite_id, satellite_id, MAX_HISTORY * 2))

    _conn.commit()


def get_history(satellite_id: int, limit: int = 6) -> list[dict]:
    """Returns the latest exchanges of a satellite in LLM message format."""
    if not _conn:
        return []
    rows = _conn.execute(
        "SELECT role, content FROM history WHERE satellite_id=? ORDER BY id DESC LIMIT ?",
        (satellite_id, limit * 2)
    ).fetchall()
    # Restore chronological order
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]



# ──------------------------- Stats and automatic adaptation ──-------------------------

# Learning thresholds
LEARN_THRESHOLD_CITY    = 5   # number of times a city is requested → default city
LEARN_THRESHOLD_HABIT   = 10  # number of times an action is repeated → habit noted
LEARN_THRESHOLD_SUGGEST = 20  # number of times → proactive suggestion in the prompt

def _get_intent_patterns() -> dict:
    """Returns intent patterns from LANG config. Empty dict if unavailable."""
    lang = _get_lang()
    if lang and hasattr(lang, "_d"):
        patterns = lang._d.get("intent_patterns")
        if patterns:
            return patterns
    return {}


def _get_stop_words() -> list[str]:
    """Returns stop words for canonical extraction from LANG config."""
    lang = _get_lang()
    if lang and hasattr(lang, "_d"):
        words = lang._d.get("intent_stop_words")
        if words:
            return words
    return []


def detect_intent(text: str) -> tuple[str, str]:
    """
    Detects the main intent of a query.
    Returns (intent, canonical) where canonical is the normalized form.
    e.g.: "Quelle est la météo à Genève ?" → ("météo", "météo genève")
    """
    import re
    lower = text.lower().strip()
    clean = re.sub(r"[?!.,;:]", "", lower).strip()
    stop_words = _get_stop_words()

    for intent, keywords in _get_intent_patterns().items():
        if any(kw in clean for kw in keywords):
            words = [w for w in clean.split()
                     if len(w) > 3 and w not in stop_words]
            canonical = intent + " " + " ".join(words[:3])
            return intent, canonical.strip()

    return "other", clean[:40]


def track_query(text: str, extra: dict | None = None):
    """
    Records a query in query_stats.
    Increments the counter if the canonical already exists.
    Called from server.py on each request.
    """
    if not _conn or not text.strip():
        return
    import json
    intent, canonical = detect_intent(text)
    extra_json = json.dumps(extra or {}, ensure_ascii=False)

    existing = _conn.execute(
        "SELECT id, count FROM query_stats WHERE canonical = ?", (canonical,)
    ).fetchone()

    if existing:
        _conn.execute(
            "UPDATE query_stats SET count = count + 1, "
            "last_seen = datetime('now'), extra = ? WHERE id = ?",
            (extra_json, existing["id"])
        )
    else:
        _conn.execute(
            "INSERT INTO query_stats (intent, canonical, count, extra) VALUES (?,?,1,?)",
            (intent, canonical, extra_json)
        )
    _conn.commit()


def get_stats(limit: int = 20) -> list[dict]:
    """Returns the most frequent queries — for the admin endpoint."""
    if not _conn:
        return []
    rows = _conn.execute(
        "SELECT intent, canonical, count, last_seen FROM query_stats "
        "ORDER BY count DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def build_adaptive_context() -> str:
    """
    Analyzes stats and generates an adaptive context block
    injected into the system prompt.
    All text comes from LANG config (multilingual).
    Returns empty string if LANG not available.
    """
    if not _conn:
        return ""

    lang = _get_lang()
    if not lang or not hasattr(lang, "_d"):
        return ""

    rows = _conn.execute(
        "SELECT intent, canonical, count FROM query_stats "
        "WHERE count >= ? ORDER BY count DESC LIMIT 10",
        (LEARN_THRESHOLD_HABIT,)
    ).fetchall()

    if not rows:
        return ""

    # Keys for weather/light/question intents — from LANG config
    weather_key  = lang._d.get("intent_weather_key",  "")
    light_key    = lang._d.get("intent_light_key",    "")
    question_key = lang._d.get("intent_question_key", "")

    # Message templates — from LANG config
    tpl_weather  = lang._d.get("memory_habit_weather",  "")
    tpl_action   = lang._d.get("memory_habit_action",   "")
    tpl_question = lang._d.get("memory_habit_question", "")
    header       = lang._d.get("memory_habits_header",  "")

    if not header:
        return ""

    lines = []
    for row in rows:
        intent    = row["intent"]
        canonical = row["canonical"]
        count     = row["count"]

        if weather_key and intent == weather_key and count >= LEARN_THRESHOLD_CITY and tpl_weather:
            words = canonical.replace(weather_key, "").strip().split()
            if words:
                lines.append(tpl_weather.format(city=words[0].capitalize(), count=count))

        elif light_key and intent == light_key and count >= LEARN_THRESHOLD_HABIT and tpl_action:
            lines.append(tpl_action.format(canonical=canonical, count=count))

        elif question_key and intent == question_key and count >= LEARN_THRESHOLD_HABIT and tpl_question:
            lines.append(tpl_question.format(canonical=canonical, count=count))

    if not lines:
        return ""

    return "\n" + header + "\n" + "\n".join(lines) + "\n"


def build_memory_context() -> str:
    """
    Builds the complete memory block injected into the system prompt:
      - Memorized facts (what the user explicitly stated)
      - Learned habits (automatically detected patterns)
    Returns empty string if nothing is known or LANG not available.
    """
    lang  = _get_lang()
    parts = []

    facts = get_facts()
    if facts and lang and hasattr(lang, "_d"):
        header = lang._d.get("memory_facts_header", "")
        if header:
            lines = "\n".join(f"- {f}" for f in facts[:15])
            parts.append(f"{header}\n{lines}")

    adaptive = build_adaptive_context()
    if adaptive.strip():
        parts.append(adaptive.strip())

    if not parts:
        return ""
    return "\n" + "\n\n".join(parts) + "\n"


#──------------------------- Automatic fact extraction from exchanges -------------------------

def _get_fact_triggers() -> list[str]:
    """Returns fact triggers from LANG config. Empty list if unavailable."""
    lang = _get_lang()
    if lang and hasattr(lang, "_d"):
        triggers = lang._d.get("fact_triggers")
        if triggers:
            return triggers
    return []


def maybe_extract_fact(user_text: str) -> bool:
    """
    Returns True if the user text contains information to memorize.
    server.py can then ask the LLM to rephrase the fact before saving it.
    """
    lower = user_text.lower()
    return any(trigger in lower for trigger in _get_fact_triggers())
