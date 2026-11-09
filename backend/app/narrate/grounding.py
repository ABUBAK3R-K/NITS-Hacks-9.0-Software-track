"""Citation validator for LLM narratives.

Rules (each one is easy to explain on a slide):
  1. A sentence must cite at least one event id.
  2. Every cited id must be in incident.evidence_events. One unknown id drops
     the whole sentence, because we cannot tell which half of it is supported.
  3. When the incident is OPEN, a sentence that talks about "the cause" and
     names a different service, without naming the engine's root cause
     service, is dropped. The engine decides the cause; the narrator does not.
Em dashes are replaced with a comma or hyphen before anything is returned.
"""
from __future__ import annotations

import re
from typing import Any

from app.schemas import SERVICE_IDS, Incident, ReportSentence

_CAUSE_WORDS = re.compile(
    r"\broot[\s-]?cause\b|\bthe cause\b|\bcaused (?:the|this)\b|\bculprit\b|\bto blame\b|\bresponsible for\b",
    re.IGNORECASE,
)


def sanitize(text: str) -> str:
    """Remove em dashes (and en dashes used as em dashes), tidy spacing."""
    text = str(text)
    text = re.sub(r"\s*\u2014\s*", ", ", text)
    text = re.sub(r"\s+\u2013\s+", ", ", text)
    text = text.replace("\u2013", "-")
    # LLMs emit non-breaking / unicode hyphens (U+2010, U+2011) in service names like
    # ledger-svc; normalise so names match, copy-paste and search work.
    text = text.replace("\u2010", "-").replace("\u2011", "-")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" ,", ",").replace(",,", ",")
    return text


def known_services(incident: Incident) -> set[str]:
    names = set(SERVICE_IDS) | set(incident.services) | set(incident.impact.services)
    names |= {c.service for c in incident.candidates}
    return names


def _mentions(text: str, service: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(service)}(?![\w-])", text, re.IGNORECASE) is not None


def blames_other_service(text: str, incident: Incident) -> bool:
    """True when an OPEN incident's sentence names a cause other than the engine's."""
    if incident.status != "OPEN" or incident.root_cause is None:
        return False
    if not _CAUSE_WORDS.search(text):
        return False
    root = incident.root_cause.service
    if _mentions(text, root):
        return False
    others = known_services(incident) - {root}
    return any(_mentions(text, s) for s in others)


def ground(raw: Any, incident: Incident) -> tuple[list[ReportSentence], int]:
    """Validate raw LLM sentences against the incident evidence set.

    `raw` is the parsed `sentences` value from the model. Returns the surviving
    sentences and the number dropped. Items that are not even sentence shaped
    count as dropped too.
    """
    known = {e.id for e in incident.evidence_events}
    kept: list[ReportSentence] = []
    dropped = 0
    if not isinstance(raw, list):
        return [], 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        text = item.get("text")
        cites = item.get("cites")
        if not isinstance(text, str) or not text.strip() or not isinstance(cites, list):
            dropped += 1
            continue
        cites = [str(c).strip() for c in cites]
        if not cites or any(c not in known for c in cites):
            dropped += 1
            continue
        text = sanitize(text)
        if blames_other_service(text, incident):
            dropped += 1
            continue
        # keep order, drop duplicate ids
        kept.append(ReportSentence(text=text, cites=list(dict.fromkeys(cites))))
    return kept, dropped
