"""U5 lifecycle, computed from events alone (event time, never the wall clock).

state(t), highest rule first:
  RESOLVED          every incident service has been within baseline (no
                    anomalous point) for resolved_quiet_s at t
  MITIGATING        a change event named fix_applied on an incident service,
                    at or after opened_at and at or before t
  SPREADING         the anomalous set grew (a service's onset) in the last
                    spreading_window_s before t
  CAUSE_IDENTIFIED  the engine decided (status not ABSTAINED)
  DETECTED          otherwise

The incident is "detected" when analyze() would first return it: at the
second service's onset, or settle_single_s after the onset of a lone service.
The history evaluates state(t) at every moment one of the rules can flip and
keeps the transitions. mttd_s / mttr_s are NOT computed here: the engine never
knows when a fault was injected.
"""
from __future__ import annotations

from .common import Episode, iso


def compute(group: dict[str, Episode], abstained: bool, fixes: list[float], t_end: float,
            cfg: dict) -> tuple[str, list[dict]]:
    onsets = sorted(ep.onset for ep in group.values())
    t_open = onsets[0]
    detected = onsets[1] if len(onsets) > 1 else t_open + cfg["settle_single_s"]
    detected = min(detected, t_end)
    growth = [t for t in onsets[1:] if t >= detected]  # the second onset (formation) counts as growth
    win = cfg["spreading_window_s"]
    t_last = max(ep.last for ep in group.values())
    quiet = cfg["resolved_quiet_s"]
    fix_ts = sorted(t for t in fixes if t_open <= t <= t_end)

    def state(t: float) -> str:
        if t - t_last >= quiet:
            return "RESOLVED"
        if fix_ts and fix_ts[0] <= t:
            return "MITIGATING"
        if any(t - win < g <= t for g in growth):
            return "SPREADING"
        if not abstained:
            return "CAUSE_IDENTIFIED"
        return "DETECTED"

    marks = {detected, t_end}
    marks.update(growth)
    marks.update(g + win for g in growth)
    marks.update(fix_ts)
    marks.add(t_last + quiet)
    history: list[dict] = [{"state": "DETECTED", "ts": iso(detected)}]
    for t in sorted(m for m in marks if detected <= m <= t_end):
        s = state(t)
        if s != history[-1]["state"]:
            history.append({"state": s, "ts": iso(t)})
    return history[-1]["state"], history
