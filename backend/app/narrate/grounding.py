"""Citation validator for LLM narratives.

Rules (each one is easy to explain on a slide):
  1. A sentence must cite at least one event id.
  2. Every cited id must be in incident.evidence_events. One unknown id drops
     the whole sentence, because we cannot tell which half of it is supported.
  3. When the incident is OPEN, a sentence that talks about "the cause" and
     names a different service, without naming the engine's root cause
     service, is dropped. The engine decides the cause; the narrator does not.
  4. A causal claim between two services ("X caused Y", "X led to Y", "Y failed
     because of X") is dropped when Y has no dependency path down to X in the
     topology: a failure only flows from a callee up to its callers.
Em dashes are replaced with a comma or hyphen before anything is returned.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Optional

import networkx as nx

from app.schemas import SERVICE_IDS, Incident, ReportSentence, Topology
from app.sim.topology import load_topology

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


# Rule 4. "X <verb> Y": X is the cause, Y the effect. "Y <phrase> X": the reverse; a
# sentence that opens with the phrase ("Because of X, Y ...") names the cause first.
_CAUSES = re.compile(
    r"\b(?:caus(?:ed|es|ing)(?!\s+by\b)|lead(?:s|ing)? to|led to|br(?:oke|eaks?|eaking)|"
    r"(?:took|takes|brought|brings) down|trigger(?:ed|s|ing)?(?!\s+by\b)|result(?:ed|s)? in|"
    r"cascad(?:ed|es|ing) (?:in)?to|propagat(?:ed|es|ing) to|spread to)\b",
    re.IGNORECASE,
)
_CAUSED_BY = re.compile(
    r"\b(?:because of|due to|caused by|triggered by|as a result of|resulting from)\b", re.IGNORECASE)
_NEGATED = re.compile(r"(?:\b(?:not|never|no)\s+(?:\w+\s+)?|n't\s+(?:\w+\s+)?)$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _default_graph() -> nx.DiGraph:
    return _graph(load_topology())


def _graph(topology: Topology) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(s.id for s in topology.services)
    g.add_edges_from((e.from_, e.to) for e in topology.edges)  # caller -> callee
    return g


def _service_mentions(text: str, services: list[str]) -> list[tuple[int, int, str]]:
    out = []
    for svc in services:
        for m in re.finditer(rf"(?<![\w-]){re.escape(svc)}(?![\w-])", text, re.IGNORECASE):
            out.append((m.start(), m.end(), svc))
    return sorted(out)


def causal_claims(text: str, services: list[str]) -> list[tuple[str, str]]:
    """(cause, effect) service pairs a sentence asserts. Nearest service on each side
    of the causal phrase; negated phrases ("did not cause") assert nothing."""
    mentions = _service_mentions(text, services)
    pairs: list[tuple[str, str]] = []

    def before(pos: int) -> Optional[str]:
        prior = [svc for _s, e, svc in mentions if e <= pos]
        return prior[-1] if prior else None

    def after(pos: int, skip: Optional[str] = None) -> Optional[str]:
        return next((svc for s, _e, svc in mentions if s >= pos and svc != skip), None)

    for m in _CAUSES.finditer(text):
        if _NEGATED.search(text[:m.start()]):
            continue
        cause, effect = before(m.start()), after(m.end())
        if cause and effect and cause != effect:
            pairs.append((cause, effect))
    for m in _CAUSED_BY.finditer(text):
        if _NEGATED.search(text[:m.start()]):
            continue
        effect = before(m.start())
        if effect is not None:
            cause = after(m.end())
        else:  # "Because of X, Y ..."
            cause = after(m.end())
            first = next((s for s, _e, svc in mentions if s >= m.end() and svc == cause), None)
            effect = after(first + 1, skip=cause) if first is not None else None
        if cause and effect and cause != effect:
            pairs.append((cause, effect))
    return pairs


def impossible_causal_claim(text: str, topology: Optional[Topology] = None) -> Optional[tuple[str, str]]:
    """The first (cause, effect) claim where the effect does not depend, directly or
    transitively, on the cause. None when every claim is possible."""
    g = _default_graph() if topology is None else _graph(topology)
    for cause, effect in causal_claims(text, list(g.nodes)):
        if not nx.has_path(g, effect, cause):
            return cause, effect
    return None


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
        if impossible_causal_claim(text) is not None:
            dropped += 1
            continue
        # keep order, drop duplicate ids
        kept.append(ReportSentence(text=text, cites=list(dict.fromkeys(cites))))
    return kept, dropped
