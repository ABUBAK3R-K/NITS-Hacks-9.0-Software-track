"""Incident history and "seen this before" similarity. Lives next to the store, never in
the engine (the engine stays pure: same events in, same ranking out).

similarity(a, b) = 0.5 if both have a root cause and it is the same service
                 + 0.3 if both have a root signature and it is the same
                 + 0.2 * Jaccard(a.services, b.services)
rounded to 2 dp. A past incident is listed only if similarity >= 0.5, at most 3,
most similar first (ties: most recent first). An incident is never compared with
itself: history entries are keyed by (namespace, incident id), where the namespace
is the live store generation or the sandbox run id.

History keeps the last 50 incidents that ended: RESOLVED, or dropped from the live
list (reset, fault expiry, or replaced by the engine), plus every sandbox-run
incident. It survives /api/sim/reset on purpose: that is what makes "seen before"
work in a demo.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.schemas import Incident, SimilarIncident

HISTORY_MAX = 50
SIMILAR_MAX = 3
SIMILAR_MIN = 0.5
W_ROOT, W_SIGNATURE, W_SERVICES = 0.5, 0.3, 0.2


@dataclass(frozen=True)
class PastIncident:
    incident_id: str
    opened_at: str
    status: str
    root_service: Optional[str]
    signature: Optional[str]
    services: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, inc: Incident) -> "PastIncident":
        rc = inc.root_cause
        return cls(incident_id=inc.id, opened_at=inc.opened_at, status=inc.status,
                   root_service=rc.service if rc else None, signature=rc.signature if rc else None,
                   services=frozenset(inc.services))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(a: PastIncident, b: PastIncident) -> float:
    s = 0.0
    if a.root_service and a.root_service == b.root_service:
        s += W_ROOT
    if a.signature and a.signature == b.signature:
        s += W_SIGNATURE
    s += W_SERVICES * jaccard(a.services, b.services)
    return round(s, 2)


class IncidentHistory:
    """Bounded, thread-safe (sandbox runs record from worker threads). Recording the
    same (namespace, id) again replaces the entry and makes it the most recent."""

    def __init__(self, max_items: int = HISTORY_MAX):
        self.max_items = max_items
        self._items: OrderedDict[tuple[str, str], PastIncident] = OrderedDict()
        self._lock = threading.Lock()

    def record(self, namespace: str, inc: Incident) -> None:
        key = (namespace, inc.id)
        with self._lock:
            self._items.pop(key, None)
            self._items[key] = PastIncident.of(inc)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def record_all(self, namespace: str, incidents: Iterable[Incident]) -> None:
        for inc in incidents:
            self.record(namespace, inc)

    def similar_to(self, namespace: str, inc: Incident, limit: int = SIMILAR_MAX,
                   min_sim: float = SIMILAR_MIN) -> list[SimilarIncident]:
        me = PastIncident.of(inc)
        with self._lock:
            items = [(k, p) for k, p in self._items.items() if k != (namespace, inc.id)]
        scored = [(similarity(me, p), p) for _k, p in items]
        scored = [(s, p) for s, p in scored if s >= min_sim]
        scored.sort(key=lambda sp: (-sp[0], _neg_ts(sp[1].opened_at)))
        return [SimilarIncident(incident_id=p.incident_id, opened_at=p.opened_at, status=p.status,
                                root_service=p.root_service, signature=p.signature,
                                services=sorted(p.services), similarity=s)
                for s, p in scored[:limit]]

    def with_similar(self, namespace: str, incidents: Iterable[Incident]) -> list[Incident]:
        return [i.model_copy(update={"similar": self.similar_to(namespace, i)}) for i in incidents]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


def _neg_ts(ts: str) -> tuple:
    """Sort key putting later ISO timestamps first."""
    return tuple(-ord(c) for c in ts)
