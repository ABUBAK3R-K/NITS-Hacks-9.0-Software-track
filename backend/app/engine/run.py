"""Lane B entry point. Pure and deterministic: no I/O, no clock, no LLM.

    analyze(events, topology) -> list[Incident]

Call it with the whole recent event list (any order, duplicates allowed);
it sorts by (ts, id) first, so the input order never matters. "Now" is the
latest event ts in the input, never the wall clock.
"""
from __future__ import annotations

import hashlib

from app.schemas import Event, Incident, Noise, RootCause, Topology

from . import anomaly, grouping, impact, lifecycle, rca, recommend, timeline, whynot
from .common import SEV_NAME, Episode, TopoIndex, event_status, iso, parse_ts
from .config import CONFIG


def incident_id(opening_service: str, opened_at: str) -> str:
    """Stable as the event list grows: depends only on the first anomalous
    service and its onset, not on how many incidents came before."""
    h = hashlib.sha1(f"{opening_service}|{opened_at}".encode()).hexdigest()[:10]
    return f"inc_{h}"


def analyze(events: list[Event], topology: Topology, config: dict | None = None) -> list[Incident]:
    cfg = CONFIG if config is None else {**CONFIG, **config}
    keyed = []
    for i, e in enumerate(events):
        t = parse_ts(e.ts)
        if t is not None:
            keyed.append((t, e.id, i))
    keyed.sort()  # (ts, id); the index only breaks ties between exact duplicates
    seen: set[str] = set()
    evs: list[Event] = []
    ts: list[float] = []
    changes: dict[str, list[tuple[float, Event]]] = {}
    fixes: dict[str, list[float]] = {}  # fix_applied remediations: lifecycle only, never a deploy
    fix_name = cfg["fix_event_name"]
    failed: list[tuple[float, Event]] = []
    alerts_by_svc: dict[str, list[float]] = {}
    for t, eid, i in keyed:
        if eid in seen:
            continue
        seen.add(eid)
        e = events[i]
        evs.append(e)
        ts.append(t)
        k = e.kind
        if k == "change":
            if e.name == fix_name:
                fixes.setdefault(e.service, []).append(t)
            else:
                changes.setdefault(e.service, []).append((t, e))
        elif k == "request":
            st = event_status(e)
            if st is not None and st >= 500:
                failed.append((t, e))
        elif k == "alert":
            alerts_by_svc.setdefault(e.service, []).append(t)
    if not evs:
        return []
    t_end = ts[-1]
    topo = TopoIndex(topology)
    failed_ts = [t for t, _ in failed]

    res = anomaly.detect(evs, ts, cfg)
    groups = grouping.group(res.episodes, topo, cfg)
    ctx = {"topo": topo, "cfg": cfg, "changes": changes, "failed": failed, "failed_ts": failed_ts,
           "alerts": alerts_by_svc, "fixes": fixes, "scrape": res.scrape_interval, "t_end": t_end,
           "by_id": {e.id: (t, e) for t, e in zip(ts, evs)}}
    # A lone service that turned anomalous in the last settle_s seconds is held
    # back for a moment: the edge often pages before the services between it
    # and the root are confirmed, and a one-second ghost incident would flicker.
    settle = cfg["settle_single_s"]
    groups = [g for g in groups if len(g) > 1 or t_end - next(iter(g.values())).onset >= settle]
    out = [_build(g, ctx) for g in groups]
    out.sort(key=lambda i: (i.opened_at, i.id))
    # Noise: this incident's alerts vs the number of incidents the same input collapsed into.
    n = len(out)
    for inc in out:
        inc.noise = Noise(alerts=inc.alert_count, incidents=n, ratio=round(inc.alert_count / n, 1))
    return out


def _build(group: dict[str, Episode], ctx: dict) -> Incident:
    topo, cfg, t_end = ctx["topo"], ctx["cfg"], ctx["t_end"]
    cands = rca.score_group(group, topo, ctx["changes"], cfg)
    verdict = rca.decide(cands, group, topo, ctx["scrape"], cfg)
    top1 = cands[0]
    t_open = min(ep.onset for ep in group.values())
    opening = min(group, key=lambda s: (group[s].onset, s))
    t_last = max(ep.last for ep in group.values())
    resolved = t_end - t_last > cfg["resolve_after_s"]
    t_to = t_last + cfg["resolve_after_s"] if resolved else t_end

    if verdict.abstain:
        status = "RESOLVED" if resolved else "ABSTAINED"
        root = None
        b = cands[1].service if len(cands) > 1 else None
        title = f"{top1.service} or {b} (undecided)" if b else f"{top1.service}: {top1.signature} (undecided)"
    else:
        status = "RESOLVED" if resolved else "OPEN"
        root = RootCause(service=top1.service, signature=top1.signature, confidence=top1.score,
                         evidence=list(top1.evidence))
        title = f"{top1.service}: {top1.signature}"

    alert_count = sum(1 for svc in group for t in ctx["alerts"].get(svc, ()) if t_open <= t <= t_to)
    sev = max(p.sev for ep in group.values() for p in ep.points)

    why = whynot.build(cands, verdict.abstain, group, topo, cfg)
    candidates = [{
        "service": c.service, "score": c.score, "components": c.comps, "penalty": c.penalty,
        "victim_of": c.victim_of, "onset": iso(c.onset), "signature": c.signature,
        "evidence": c.evidence, "component_evidence": c.ce,
        "ruled_out_reason": why.get(c.service, (None, []))[0],
        "ruled_out_evidence": why.get(c.service, (None, []))[1],
    } for c in cands]
    fixes = [t for svc in group for t in ctx["fixes"].get(svc, ())]
    state, history = lifecycle.compute(group, verdict.abstain, fixes, t_end, cfg)
    tl = timeline.build(group, topo, cands)
    recs = recommend.build(verdict, group, topo)
    imp = impact.compute(group, topo, ctx["failed"], ctx["failed_ts"], t_open, t_to)

    ids: list[str] = []
    if root:
        ids += root.evidence
    for c in cands:
        ids += c.evidence
        for k in ("upstream", "precedence", "change", "severity"):
            ids += c.ce[k]
        ids += why.get(c.service, (None, []))[1]
    for row in tl:
        ids += row.event_ids
    for r in recs:
        ids += r.evidence
    by_id = ctx["by_id"]
    found = sorted((by_id[i] for i in dict.fromkeys(ids) if i in by_id), key=lambda te: (te[0], te[1].id))

    opened_at = iso(t_open)
    return Incident.model_validate({
        "id": incident_id(opening, opened_at),
        "title": title,
        "status": status,
        "opened_at": opened_at,
        "updated_at": iso(t_last),
        "services": sorted(group, key=lambda s: (group[s].onset, s)),
        "alert_count": alert_count,
        "severity": SEV_NAME[max(0, min(3, sev))],
        "root_cause": root,
        "candidates": candidates,
        "abstain_reason": verdict.abstain_reason,
        "disambiguation": verdict.disambiguation,
        "impact": imp,
        "timeline": tl,
        "recommendations": recs,
        "evidence_events": [e for _, e in found],
        "lifecycle": state,
        "lifecycle_history": history,
    })
