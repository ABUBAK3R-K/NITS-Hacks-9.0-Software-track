"""Small shared helpers: timestamps, severities, topology index, data holders."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.schemas import Event, Topology

SEV_RANK = {"info": 0, "warn": 1, "error": 2, "critical": 3}
SEV_NAME = {v: k for k, v in SEV_RANK.items()}


_MINUTE_CACHE: dict[str, float] = {}


def parse_ts(s: str) -> Optional[float]:
    """ISO 8601 -> epoch seconds (UTC). Naive timestamps are treated as UTC.
    Fast path for the canonical "YYYY-MM-DDTHH:MM:SS.mmmZ": the minute prefix
    is parsed once and cached (a pure function of the string, so still
    deterministic)."""
    if len(s) == 24 and s[23] == "Z" and s[19] == ".":
        base = _MINUTE_CACHE.get(s[:16])
        if base is None:
            try:
                base = datetime.fromisoformat(s[:16] + ":00+00:00").timestamp()
            except ValueError:
                base = None
            if base is not None:
                if len(_MINUTE_CACHE) > 100_000:
                    _MINUTE_CACHE.clear()
                _MINUTE_CACHE[s[:16]] = base
        if base is not None:
            try:
                return base + float(s[17:23])
            except ValueError:
                pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00") if s.endswith("Z") else s)
    except (ValueError, AttributeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def iso(t: float) -> str:
    """epoch seconds -> canonical "YYYY-MM-DDTHH:MM:SS.mmmZ"."""
    ms = int(round(t * 1000))
    d = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def fmt_num(v: float) -> str:
    a = abs(v)
    if a >= 100:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.1f}"
    if a >= 1:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if a == 0:
        return "0"
    return f"{v:.3g}"


def fmt_metric(name: str, v: float) -> str:
    if name.endswith("_ms"):
        return f"{fmt_num(v)} ms"
    if name.endswith("_pct"):
        return f"{fmt_num(v)}%"
    return fmt_num(v)


@dataclass
class Point:
    """One anomalous observation on a service."""

    ts: float
    event_id: str
    kind: str            # metric | alert | log | request | silence
    signal: str          # metric name, alert name, "log", "http_request", "silence"
    magnitude: float     # comparable across kinds, capped at z_cap
    sev: int             # SEV_RANK
    value: Optional[float] = None
    baseline: Optional[float] = None
    z: Optional[float] = None
    text: str = ""       # human readable description


@dataclass
class Episode:
    service: str
    points: list[Point] = field(default_factory=list)

    @property
    def onset(self) -> float:
        return self.points[0].ts

    @property
    def last(self) -> float:
        return self.points[-1].ts

    @property
    def onset_point(self) -> Point:
        return self.points[0]

    @property
    def peak_point(self) -> Point:
        # max magnitude, earliest on ties (points are time sorted)
        best = self.points[0]
        for p in self.points:
            if p.magnitude > best.magnitude:
                best = p
        return best


class TopoIndex:
    def __init__(self, topology: Topology):
        self.tier = {s.id: s.tier for s in topology.services}
        self.label = {s.id: s.label for s in topology.services}
        self.deps: dict[str, list[str]] = {}
        self.callers: dict[str, list[str]] = {}
        for e in topology.edges:
            self.deps.setdefault(e.from_, []).append(e.to)
            self.callers.setdefault(e.to, []).append(e.from_)
        for d in (self.deps, self.callers):
            for k in d:
                d[k] = sorted(set(d[k]))
        self._closure: dict[str, frozenset[str]] = {}

    def adjacent(self, a: str, b: str) -> bool:
        return b in self.deps.get(a, ()) or a in self.deps.get(b, ())

    def callers_closure(self, svc: str) -> frozenset[str]:
        """Services that transitively depend on (call) svc."""
        got = self._closure.get(svc)
        if got is not None:
            return got
        out: set[str] = set()
        stack = [svc]
        while stack:
            cur = stack.pop()
            for c in self.callers.get(cur, ()):
                if c not in out and c != svc:
                    out.add(c)
                    stack.append(c)
        res = frozenset(out)
        self._closure[svc] = res
        return res


def event_status(e: Event) -> Optional[int]:
    s = e.attrs.get("status") if e.attrs else None
    if type(s) is int:
        return s
    if s is None or isinstance(s, bool):
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None
