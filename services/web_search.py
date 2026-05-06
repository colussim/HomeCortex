"""
services/web_search.py — Web search via DuckDuckGo Instant Answer API

No API key, no account, no cost.
Used by Kira when the LLM does not know a person or a topic.

Examples of questions that trigger this tool:
  "Who is Emmanuel Macron?"
  "What is the CERN?"
  "What is the height of Mount Everest?"
  "Who invented the telephone?"

DuckDuckGo Instant Answer returns:
  - AbstractText : Wikipedia summary of the entity
  - Answer       : direct answer for factual questions
  - Definition   : definition for terms
"""

import requests

try:
    from services.config_loader import LANG as _LANG
except ImportError:
    _LANG = None

DDGO_URL = "https://api.duckduckgo.com/"
TIMEOUT  = 6   # seconds — quick or we give up


def run(query: str) -> str:
    """
    Searches for information on DuckDuckGo and returns
    a short phrase ready to be read aloud.

    Args:
        query : question or topic to search for (ex: "Emmanuel Macron", "CERN")

    Returns:
        str : concise answer in French, or failure message
    """
    try:
        r = requests.get(
            DDGO_URL,
            params={
                "q":              query,
                "format":         "json",
                "no_html":        "1",
                "skip_disambig":  "1",
                "no_redirect":    "1",
                "kl": (_LANG._d["ddg_locale"]
                       if _LANG and hasattr(_LANG, "_d") else "fr-fr"),
            },
            timeout=TIMEOUT,
            headers={"User-Agent": "Kira-VoiceAssistant/1.0"}
        )
        r.raise_for_status()
        data = r.json()

    except requests.exceptions.Timeout:
        return (_LANG._d.get("search_timeout", "Search timeout.")
                if _LANG and hasattr(_LANG, "_d") else "Search timeout.")
    except Exception as e:
        msg = (_LANG._d.get("search_error", "Search error: {error}")
               if _LANG and hasattr(_LANG, "_d") else "Search error: {error}")
        return msg.format(error=str(e)[:60])

    # ── Priority 1: direct answer (calculations, conversions, precise facts)
    answer = (data.get("Answer") or "").strip()
    if answer and len(answer) < 300:
        return _clean(answer)

    # ── Priority 2: Wikipedia summary (people, places, concepts)
    abstract = (data.get("AbstractText") or "").strip()
    if abstract:
        # Truncate to 2 sentences max for voice reading
        sentences = abstract.replace("! ", ". ").replace("? ", ". ").split(". ")
        short = ". ".join(s.strip() for s in sentences[:2] if s.strip())
        if short and not short.endswith("."):
            short += "."
        return _clean(short)

    # ── Priority 3: definition
    definition = (data.get("Definition") or "").strip()
    if definition and len(definition) < 300:
        return _clean(definition)

    # ── Priority 4: related topics (if no direct answer)
    related = data.get("RelatedTopics", [])
    for topic in related[:2]:
        if isinstance(topic, dict):
            text = (topic.get("Text") or "").strip()
            if text and len(text) > 30:
                sentences = text.split(". ")
                return _clean(sentences[0] + ".")

    # ── Failure: no information found
    msg = (_LANG._d.get("search_not_found", "No information found about {query}.")
           if _LANG and hasattr(_LANG, "_d") else "No information found about {query}.")
    return msg.format(query=query)


def _clean(text: str) -> str:
    """Cleans up text for speech reading — removes HTML/wiki artifacts."""
    import re
    # Remove wiki references [1], [2]...
    text = re.sub(r"\[\d+\]", "", text)
    # Remove residual HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Normalize spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Steve Jobs"
    print(f"Search : '{query}'")
    print(f"Result : {run(query)}")
