"""Step 3 + 4: score every anomalous service in an incident, pick a signature,
apply the abstain rule and write "what data would disambiguate".

Formulas (DECISIONS.md 14:16), components stored already weighted:
  upstream   = anomalous services that transitively call it / (anomalous count - 1)
               (a single-service incident has nothing to compare against, so the
               share is vacuously 1.0; see DECISIONS.md)
  precedence = share of its anomalous transitive callers whose onset is later
               than its own (0 if it has none)
  change     = 1 if a change event on it within 10 min before its onset
  severity   = its magnitude / max magnitude in the incident
  score      = sum(components) * penalty, penalty = 0.5 if it has an anomalous
               direct dependency (it is probably a victim)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.schemas import Event

from .common import Episode, TopoIndex


@dataclass
class Scored:
    service: str
    score: float
    comps: dict[str, float]
    penalty: float
    victim_of: Optional[str]
    onset: float
    signature: str
    evidence: list[str]
    ce: dict[str, list[str]]
    change_event: Optional[Event] = None


@dataclass
class Verdict:
    candidates: list[Scored]
    abstain: bool
    abstain_reason: Optional[str] = None
    disambiguation: list[str] = field(default_factory=list)


def _contains(text: str, patterns) -> bool:
    t = text.lower()
    return any(p in t for p in patterns)


def is_error_metric(name: str) -> bool:
    n = name.lower()
    return "error" in n or "fail" in n or "5xx" in n


def errors_dominate(pts, cfg: dict) -> bool:
    """Errors dominate when an anomalous error metric reaches error_dominant_rate
    (a quarter of requests failing by default)."""
    return any(p.kind == "metric" and is_error_metric(p.signal) and (p.value or 0) >= cfg["error_dominant_rate"]
               for p in pts)


def signature(ep: Episode, has_change: bool, cfg: dict) -> str:
    pts = ep.points
    for p in pts:
        if p.kind == "metric" and p.signal == "db_pool_in_use_pct" and (p.value or 0) >= cfg["pool_saturated_pct"]:
            return "connection_pool_exhausted"
        if p.kind == "log" and _contains(p.text, cfg["pool_log_patterns"]):
            return "connection_pool_exhausted"
        if p.kind == "alert" and _contains(p.signal, ("pool", "connectionssaturated")):
            return "connection_pool_exhausted"
    if has_change:
        return "error_spike_after_deploy"
    if any(p.kind == "silence" or (p.kind == "metric" and is_error_metric(p.signal)
                                   and (p.value or 0) >= cfg["down_error_rate"]) for p in pts):
        return "dependency_down"
    if any(p.kind == "log" and _contains(p.text, cfg["down_log_patterns"]) for p in pts):
        return "dependency_down"
    metrics = [p for p in pts if p.kind == "metric"]
    if metrics:
        if errors_dominate(metrics, cfg):
            return "error_spike"
        if any(not is_error_metric(p.signal) for p in metrics):
            return "latency_spike"
        return "error_spike"
    if any(p.kind == "alert" and _contains(p.signal, ("latency", "slow", "p95", "p99")) for p in pts):
        return "latency_spike"
    return "error_spike"


def _uniq(xs) -> list[str]:
    return list(dict.fromkeys(xs))


def score_group(group: dict[str, Episode], topo: TopoIndex, changes: dict[str, list[tuple[float, Event]]],
                cfg: dict) -> list[Scored]:
    w = cfg["weights"]
    onset = {s: ep.onset for s, ep in group.items()}
    onset_id = {s: ep.onset_point.event_id for s, ep in group.items()}
    names = sorted(group, key=lambda s: (onset[s], s))
    max_mag = max(ep.peak_point.magnitude for ep in group.values()) or 1.0
    out: list[Scored] = []
    for svc in names:
        ep = group[svc]
        others = [o for o in names if o != svc]
        closure = topo.callers_closure(svc)
        dependents = [o for o in others if o in closure]
        up = len(dependents) / len(others) if others else 1.0
        later = [d for d in dependents if onset[d] > onset[svc]]
        prec = len(later) / len(dependents) if dependents else 0.0
        chg_ev = None
        for t, ce in changes.get(svc, ()):
            if onset[svc] - cfg["change_lookback_s"] <= t <= onset[svc]:
                chg_ev = ce  # latest qualifying change (list is time sorted)
        peak = ep.peak_point
        sev = peak.magnitude / max_mag
        comps = {"upstream": round(w["upstream"] * up, 3),
                 "precedence": round(w["precedence"] * prec, 3),
                 "change": round(w["change"] * (1.0 if chg_ev else 0.0), 3),
                 "severity": round(w["severity"] * sev, 3)}
        anom_deps = [d for d in topo.deps.get(svc, ()) if d in group]
        victim_of = min(anom_deps, key=lambda d: (onset[d], d)) if anom_deps else None
        penalty = cfg["victim_penalty"] if anom_deps else 1.0
        ce = {"upstream": _uniq(onset_id[d] for d in dependents),
              "precedence": _uniq([onset_id[svc]] + [onset_id[d] for d in later]),
              "change": [chg_ev.id] if chg_ev else [],
              "severity": [peak.event_id]}
        evidence = _uniq(x for k in ("upstream", "precedence", "change", "severity") for x in ce[k])
        out.append(Scored(svc, round(sum(comps.values()) * penalty, 3), comps, penalty, victim_of, onset[svc],
                          signature(ep, chg_ev is not None, cfg), evidence, ce, chg_ev))
    out.sort(key=lambda c: (-c.score, c.onset, c.service))
    return out


def decide(cands: list[Scored], group: dict[str, Episode], topo: TopoIndex, scrape: dict[str, float],
           cfg: dict) -> Verdict:
    top1 = cands[0]
    top2 = cands[1] if len(cands) > 1 else None
    margin = round(top1.score - (top2.score if top2 else 0.0), 3)
    min_margin, min_score = cfg["abstain_min_margin"], cfg["abstain_min_score"]
    reasons = []
    if top2 is not None and margin < min_margin:
        reasons.append(f"top two candidates {top1.service} ({top1.score:.3f}) and {top2.service} "
                       f"({top2.score:.3f}) differ by {margin:.3f} (< {min_margin})")
    if top1.score < min_score:
        reasons.append(f"top score {top1.score:.3f} ({top1.service}) is below {min_score}")
    if not reasons:
        return Verdict(cands, False)
    return Verdict(cands, True, "; ".join(reasons), disambiguate(top1, top2, group, topo, scrape, cfg))


def disambiguate(a: Scored, b: Optional[Scored], group: dict[str, Episode], topo: TopoIndex,
                 scrape: dict[str, float], cfg: dict) -> list[str]:
    out: list[str] = []
    pair = [c for c in (a, b) if c is not None]
    for c in pair:
        if c.change_event is not None:
            attrs = c.change_event.attrs or {}
            ver, prev = attrs.get("version"), attrs.get("previous_version")
            split = f"{prev} vs {ver}" if ver and prev else f"before vs after {ver}" if ver else "old vs new replicas"
            out.append(f"{c.service} error_rate split by version ({split}), to see whether only the new "
                       f"replicas fail")
    if b is not None:
        gap = abs(a.onset - b.onset)
        interval = max(scrape.get(a.service, 0.0), scrape.get(b.service, 0.0)) or cfg["scrape_interval_default_s"]
        if gap <= cfg["close_onset_intervals"] * interval:
            out.append(f"{a.service} and {b.service} signals at 1 s resolution (or traces): their onsets are "
                       f"{gap:.1f} s apart with a {interval:.0f} s scrape interval, so the order is not reliable")
    for c in pair:
        tier = topo.tier.get(c.service)
        if tier == "data":
            out.append(f"{c.service} lock and connection holders (for postgres: pg_stat_activity), to see "
                       f"which client is blocking it")
        elif tier == "external":
            out.append(f"{c.service} latency and errors split by provider (PSP status page), to confirm the "
                       f"external side is slow")
        elif tier is None:
            out.append(f"dependency edges for {c.service}: it is not in the topology, so upstream and "
                       f"precedence cannot be scored")
    if b is None or a.score < cfg["abstain_min_score"]:
        n = len(group[a.service].points)
        out.append(f"more signals for {a.service} (metrics or traces, not only logs and alerts): the evidence "
                   f"so far is {n} anomalous point{'s' if n != 1 else ''}")
    if b is not None:
        out.append(f"distributed traces linking {a.service} and {b.service} calls, to see which side fails first")
    return _uniq(out)
