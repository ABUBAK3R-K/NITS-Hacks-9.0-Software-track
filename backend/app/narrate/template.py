"""Deterministic narrative. Used when no LLM key is set, when every LLM call
fails, or when grounding drops every LLM sentence. Every sentence is built
from engine output and cites the event ids that output already carries, so it
is grounded by construction (and we still filter cites to the evidence set).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas import Candidate, Incident, ReportSentence

from .grounding import sanitize

IST = timezone(timedelta(hours=5, minutes=30))

_SIGNATURE_TEXT = {
    "connection_pool_exhausted": "connection pool exhausted",
    "error_spike_after_deploy": "error spike right after a deploy",
    "dependency_down": "dependency down",
    "latency_spike": "latency spike",
    "error_spike": "error spike",
}


def ist(ts: str | None) -> str:
    """'2026-11-09T08:32:05.120Z' -> '14:02:05 IST'. Falls back to the raw string."""
    if not ts:
        return "an unknown time"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%H:%M:%S IST")
    except ValueError:
        return ts


def signature_text(sig: str | None) -> str:
    if not sig:
        return "anomaly"
    return _SIGNATURE_TEXT.get(sig, sig.replace("_", " "))


def _reasons(c: Candidate) -> list[str]:
    out = []
    if c.components.upstream > 0:
        out.append("other failing services depend on it")
    if c.components.precedence > 0:
        out.append("it went wrong before the services that call it")
    if c.components.change > 0:
        out.append("a change was deployed on it shortly before its onset")
    if c.components.severity > 0:
        out.append("its anomaly is among the strongest")
    return out


def _join(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


class _Builder:
    def __init__(self, incident: Incident):
        self.known = {e.id for e in incident.evidence_events}
        self.sentences: list[ReportSentence] = []

    def add(self, text: str, cites: list[str]) -> None:
        cites = [c for c in dict.fromkeys(cites) if c in self.known]
        if cites:
            self.sentences.append(ReportSentence(text=sanitize(text), cites=cites))


def _impact_cites(incident: Incident) -> list[str]:
    root = incident.root_cause.service if incident.root_cause else None
    ids: list[str] = []
    for t in incident.timeline:
        if t.service in ("payments-svc", "auth-svc", "ledger-svc") or (t.service != root and not ids):
            ids += t.event_ids[:1]
    if not ids and incident.timeline:
        ids = incident.timeline[0].event_ids[:1]
    return ids[:3]


def _timeline(b: _Builder, incident: Incident) -> None:
    for i, t in enumerate(incident.timeline):
        when = ist(t.ts)
        if t.caused_by:
            b.add(f"At {when}, {t.service} followed, downstream of {t.caused_by}: {t.what}.", t.event_ids)
        elif i == 0:
            b.add(f"At {when}, {t.service} was the first service to go wrong: {t.what}.", t.event_ids)
        else:
            b.add(f"At {when}, {t.service} also showed a problem: {t.what}.", t.event_ids)


def _impact(b: _Builder, incident: Incident) -> None:
    im = incident.impact
    b.add(
        f"Impact so far: {im.users_affected} users affected and {im.failed_payments} failed payments "
        f"worth INR {im.failed_value_inr:,.0f}, across {len(im.services)} services.",
        _impact_cites(incident),
    )


def _recommendations(b: _Builder, incident: Incident) -> None:
    for r in incident.recommendations:
        why = r.why.rstrip(".")
        why = why[:1].upper() + why[1:]
        b.add(f"Next step: {r.action.rstrip('.')}. Why: {why}.", r.evidence)


def template_report_parts(incident: Incident) -> tuple[str, list[ReportSentence]]:
    """Returns (summary, sentences) for OPEN/RESOLVED or ABSTAINED incidents."""
    b = _Builder(incident)
    rc = incident.root_cause
    if incident.status != "ABSTAINED" and rc is not None:
        top = next((c for c in incident.candidates if c.service == rc.service), None)
        reasons = _join(_reasons(top)) if top else ""
        n_down = max(len(incident.services) - 1, 0)
        summary = (
            f"{rc.service} is the root cause ({signature_text(rc.signature)}, confidence {rc.confidence:.2f}). "
            f"{incident.alert_count} alerts fired, but they trace back to one cause: "
            f"the other {n_down} failing services are downstream effects."
        )
        line = f"The engine names {rc.service} as the root cause ({signature_text(rc.signature)}) with confidence {rc.confidence:.2f}"
        b.add(line + (f", because {reasons}." if reasons else "."), rc.evidence)
        _timeline(b, incident)
        victims = [c.service for c in incident.candidates if c.victim_of and c.service != rc.service]
        if victims and top:
            b.add(
                f"{_join(victims)} scored lower because each depends on another failing service, "
                f"so the engine treats them as victims, not causes.",
                [i for c in incident.candidates if c.service in victims for i in c.evidence[:1]],
            )
        _impact(b, incident)
        _recommendations(b, incident)
        return sanitize(summary), b.sentences

    # ABSTAINED (or OPEN without a root cause, treated the same way)
    cands = incident.candidates[:2]
    reason = (incident.abstain_reason or "the evidence does not separate the top candidates").rstrip(".")
    if len(cands) >= 2:
        c1, c2 = cands
        summary = (
            f"J.A.R.V.I.S. declines to name a single root cause. {c1.service} ({c1.score:.3f}) and "
            f"{c2.service} ({c2.score:.3f}) are too close to call: {reason}."
        )
    elif cands:
        summary = f"J.A.R.V.I.S. declines to name a root cause: {reason}."
    else:
        summary = "J.A.R.V.I.S. declines to name a root cause because there is not enough evidence yet."
    all_cand_ev = [i for c in cands for i in c.evidence[:2]]
    b.add(f"The system declines to pick a root cause because {reason}.", all_cand_ev)
    for c in cands:
        reasons = _reasons(c)
        text = f"{c.service} scores {c.score:.3f}"
        if reasons:
            text += f": {_join(reasons)}"
        if c.penalty < 1 and c.victim_of:
            text += f", but its score is halved because it depends on {c.victim_of}, which is also failing"
        b.add(text + ".", c.evidence)
    _timeline(b, incident)
    if incident.disambiguation:
        b.add("Data that would settle it: " + "; ".join(d.rstrip(".") for d in incident.disambiguation) + ".", all_cand_ev)
    _impact(b, incident)
    _recommendations(b, incident)
    return sanitize(summary), b.sentences
