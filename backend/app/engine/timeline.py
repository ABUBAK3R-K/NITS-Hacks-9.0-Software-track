"""Step 6: incident timeline.

One entry per anomalous service at its onset, annotated with caused_by = the
nearest (latest earlier) anomalous direct dependency. Change events that
produced a change component get their own entry, so the deploy shows up in
the story even when it is not blamed.
"""
from __future__ import annotations

from app.schemas import TimelineEntry

from .common import Episode, Point, TopoIndex, iso, parse_ts
from .rca import Scored, is_error_metric


def main_point(ep: Episode, sig: str | None) -> Point:
    """The point that best illustrates the signature, e.g. the pool metric for
    connection_pool_exhausted or the error metric for error_spike."""
    metrics = [p for p in ep.points if p.kind == "metric"]
    pref = []
    if sig == "connection_pool_exhausted":
        pref = [p for p in metrics if "pool" in p.signal]
    elif sig in ("error_spike", "error_spike_after_deploy"):
        pref = [p for p in metrics if is_error_metric(p.signal)]
    elif sig == "latency_spike":
        pref = [p for p in metrics if not is_error_metric(p.signal)]
    elif sig == "dependency_down":
        pref = [p for p in ep.points if p.kind == "silence"]
    pool = pref or metrics
    if not pool:
        return ep.peak_point
    return max(pool, key=lambda p: (p.value if p.kind == "metric" and p in pref else 0.0, p.magnitude, -p.ts))


def describe(ep: Episode, sig: str | None = None) -> str:
    onset = ep.onset_point
    main = main_point(ep, sig)
    what = main.text
    if onset is not main and onset.kind in ("log", "request", "silence"):
        what += f"; first signal: {onset.text}"
    return what if len(what) <= 220 else what[:217] + "..."


def build(group: dict[str, Episode], topo: TopoIndex, cands: list[Scored]) -> list[TimelineEntry]:
    sigs = {c.service: c.signature for c in cands}
    rows: list[tuple[float, str, TimelineEntry]] = []
    for svc, ep in group.items():
        earlier = [d for d in topo.deps.get(svc, ()) if d in group and group[d].onset < ep.onset]
        cause = max(earlier, key=lambda d: (group[d].onset, d)) if earlier else None
        sig = sigs.get(svc)
        ids = [ep.onset_point.event_id]
        main = main_point(ep, sig)
        for p in (main, ep.peak_point):
            if p.event_id not in ids:
                ids.append(p.event_id)
        rows.append((ep.onset, svc, TimelineEntry(ts=iso(ep.onset), service=svc, what=describe(ep, sig),
                                                  event_ids=ids, caused_by=cause)))
    for c in cands:
        ev = c.change_event
        if ev is None:
            continue
        attrs = ev.attrs or {}
        ver, prev = attrs.get("version"), attrs.get("previous_version")
        label = f"{ev.name} {prev} -> {ver}" if ver and prev else f"{ev.name} {ver}" if ver else ev.name
        what = f"change on {c.service}: {ev.message or label}"
        t = parse_ts(ev.ts) or c.onset
        rows.append((t, c.service, TimelineEntry(ts=iso(t), service=c.service, what=what[:220],
                                                 event_ids=[ev.id], caused_by=None)))
    rows.sort(key=lambda r: (r[0], r[1], r[2].what))
    return [r[2] for r in rows]
