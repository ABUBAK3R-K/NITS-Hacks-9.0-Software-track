"""U2 "Why not X?": one plain sentence per losing candidate.

For every candidate except the winner (except the top two when ABSTAINED,
since neither was ruled out), find the single factor that cost it the most
score vs the winner and say so with the engine's own numbers:

  victim      score lost to the x0.5 victim penalty = raw sum * (1 - penalty)
  precedence  winner.precedence - its precedence   (it went bad later)
  upstream    winner.upstream - its upstream       (fewer affected dependents)
  change      winner.change - its change           (no change event before onset)
  severity    winner.severity - its severity       (weaker anomaly)

Ties break in that fixed order, so the output is deterministic. Pure, no LLM.
"""
from __future__ import annotations

from typing import Optional

from .common import Episode, TopoIndex
from .rca import Scored

ORDER = ("victim", "precedence", "upstream", "change", "severity")


def _label(c: Scored) -> str:
    ev = c.change_event
    if ev is None:
        return c.service
    ver = (ev.attrs or {}).get("version")
    return f"{c.service} ({ev.name} {ver})" if ver else f"{c.service} ({ev.name})"


def _s(x: float) -> str:
    return f"{x:.1f}"


def losses(c: Scored, win: Scored) -> dict[str, float]:
    raw = sum(c.comps.values())
    out = {"victim": round(raw * (1.0 - c.penalty), 3) if c.penalty < 1.0 else 0.0}
    for k in ("precedence", "upstream", "change", "severity"):
        out[k] = round(win.comps[k] - c.comps[k], 3)
    return out


def reason(c: Scored, win: Scored, group: dict[str, Episode], topo: TopoIndex,
           cfg: dict) -> tuple[str, list[str]]:
    """(sentence, evidence ids) for candidate c losing to win."""
    loss = losses(c, win)
    best = max(ORDER, key=lambda k: (loss[k], -ORDER.index(k)))
    on_c = group[c.service].onset_point.event_id
    on_w = group[win.service].onset_point.event_id
    head = f"{_label(c)}: ruled out."
    tail = f" Score {c.score:.2f} vs {win.service} {win.score:.2f}."
    if loss[best] <= 0:
        return (f"{head} It ties {win.service} on every factor and ranks lower only because "
                f"{win.service} went bad first.{tail}", [on_c, on_w])

    if best == "victim":
        dep = c.victim_of or win.service
        on_d = group[dep].onset_point.event_id if dep in group else on_w
        dt = c.onset - group[dep].onset if dep in group else 0.0
        if dt > 0:
            lead = f"Went bad {_s(dt)} s after {dep}, and its own dependency {dep} is broken"
        else:
            lead = f"Its own dependency {dep} is broken too"
        return (f"{head} {lead}, so the victim penalty applied (x{c.penalty:g}).{tail}", [on_c, on_d])

    if best == "precedence":
        dt = c.onset - win.onset
        if dt > 0:
            return (f"{head} Went bad {_s(dt)} s after {win.service}, so {win.service} came first "
                    f"(precedence {c.comps['precedence']:.2f} vs {win.comps['precedence']:.2f}).{tail}",
                    [on_c, on_w])
        return (f"{head} The affected services that depend on it did not go bad after it "
                f"(precedence {c.comps['precedence']:.2f} vs {win.comps['precedence']:.2f}).{tail}",
                [on_c] + c.ce["precedence"][1:3] + [on_w])

    if best == "upstream":
        others = len(group) - 1
        w_up = cfg["weights"]["upstream"] or 1.0
        k_c = round(c.comps["upstream"] / w_up * others)
        k_w = round(win.comps["upstream"] / w_up * others)
        ids = [on_c] + c.ce["upstream"][:2] + win.ce["upstream"][:2]
        if k_c == 0:
            lead = f"None of the {others} other affected services depend on it"
        else:
            lead = f"Only {k_c} of the {others} other affected services {'depends' if k_c == 1 else 'depend'} on it"
        return (f"{head} {lead}, vs {k_w} for {win.service}, so it explains fewer of the failures.{tail}",
                list(dict.fromkeys(ids)))

    if best == "change":
        wev = win.change_event
        ver = (wev.attrs or {}).get("version") if wev is not None else None
        what = f"{win.service} had a {wev.name} {ver}".rstrip() if wev is not None else f"{win.service} had a change"
        mins = cfg["change_lookback_s"] / 60.0
        ids = [on_c] + ([wev.id] if wev is not None else [on_w])
        return (f"{head} No change event on it in the {mins:g} min before it went bad, while {what} "
                f"just before its errors.{tail}", ids)

    # severity
    pc, pw = group[c.service].peak_point, group[win.service].peak_point
    w_sev = cfg["weights"]["severity"] or 1.0
    rc, rw = c.comps["severity"] / w_sev, win.comps["severity"] / w_sev
    return (f"{head} Its anomaly was weaker: its peak was {rc * 100:.0f}% of the strongest signal in the "
            f"incident, vs {rw * 100:.0f}% for {win.service}.{tail}", list(dict.fromkeys([pc.event_id, pw.event_id])))


def build(cands: list[Scored], abstain: bool, group: dict[str, Episode], topo: TopoIndex,
          cfg: dict) -> dict[str, tuple[Optional[str], list[str]]]:
    """service -> (ruled_out_reason, ruled_out_evidence)."""
    keep = 2 if abstain else 1
    win = cands[0]
    out: dict[str, tuple[Optional[str], list[str]]] = {}
    for c in cands[keep:]:
        text, ids = reason(c, win, group, topo, cfg)
        out[c.service] = (text.replace(chr(0x2014), "-"), list(dict.fromkeys(ids)))
    return out
