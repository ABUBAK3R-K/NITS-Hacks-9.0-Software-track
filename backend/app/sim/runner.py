"""Offline fault runner (U1). Pure: no I/O, no clock, no sleeping.

    simulate_spec(spec)            -> (events sorted by ts, fault_injected_at)
    random_blind_spec(seed)        -> FaultSpec, random but valid, deterministic per seed
    blind_specs_for_eval(20, 0)    -> 100 FaultSpecs, 20 per kind, kinds interleaved
    first_detection(events, ...)   -> event time at which the engine first reports an incident

Used by the sandbox runs in store.py and by Lane F's eval harness. The engine is never
told the injection time: MTTD is measured here, outside it, by replaying analyze on
growing prefixes of the event stream.
"""
from __future__ import annotations

import bisect
import random
from datetime import datetime, timedelta
from typing import Callable, Optional

from app.schemas import Decoy, Event, FaultSpec, Incident, Topology
from app.sim.faults import ALL_KINDS, ActiveFault, valid_kinds
from app.sim.generator import DEFAULT_START, Simulator, iso
from app.sim.topology import service_ids


def simulate_spec(spec: FaultSpec, *, baseline_s: float = 60.0, after_s: float = 90.0,
                  start: datetime | None = None) -> tuple[list[Event], datetime]:
    """Baseline traffic for baseline_s + onset_jitter_s, then the fault, then after_s more.
    Returns (events sorted by ts, fault_injected_at). Deterministic per spec (incl. seed)."""
    _sim, f, events = start_spec(spec, baseline_s=baseline_s, after_s=after_s, start=start)
    return events, f.injected_at or f.start


def start_spec(spec: FaultSpec, *, baseline_s: float = 60.0, after_s: float = 90.0,
               start: datetime | None = None) -> tuple[Simulator, ActiveFault, list[Event]]:
    """Like simulate_spec, but also returns the simulator and the fault, so a sandbox run
    can be continued later (U5 apply_fix), still fast-forwarded."""
    start = start or DEFAULT_START
    sim = Simulator(start, seed=spec.seed)
    at = start + timedelta(seconds=baseline_s + spec.onset_jitter_s)
    f = sim.inject_spec(spec, at, past_ok=True)  # nothing generated yet, so the decoy may precede `at`
    events = sim.advance(at + timedelta(seconds=after_s))
    return sim, f, events


def _random_decoy(rng: random.Random, target: str) -> Optional[Decoy]:
    if rng.random() >= 0.5:
        return None
    others = [s for s in service_ids() if s != target]
    return Decoy(service=rng.choice(others), offset_s=round(rng.uniform(-60.0, 30.0), 1))


def random_blind_spec(seed: int) -> FaultSpec:
    """Random target, random kind valid for it, onset jitter 0..20 s, and a decoy deploy
    on another service at -60..+30 s half the time."""
    rng = random.Random(seed)
    target = rng.choice(service_ids())
    kind = rng.choice(valid_kinds(target))
    jitter = round(rng.uniform(0.0, 20.0), 1)
    return FaultSpec(target=target, kind=kind, onset_jitter_s=jitter, decoy=_random_decoy(rng, target),
                     seed=seed)


def _spec_for_kind(kind: str, seed: int) -> FaultSpec:
    rng = random.Random(seed)
    targets = [s for s in service_ids() if kind in valid_kinds(s)]
    target = rng.choice(targets)
    jitter = round(rng.uniform(0.0, 20.0), 1)
    return FaultSpec(target=target, kind=kind, onset_jitter_s=jitter, decoy=_random_decoy(rng, target),
                     seed=seed)


def blind_specs_for_eval(n_per_kind: int = 20, seed0: int = 0) -> list[FaultSpec]:
    """n_per_kind specs per fault kind. Spec i uses seed seed0 + i and kind ALL_KINDS[i % 5],
    so any contiguous seed range (e.g. tune on 0-49, report on 50-99) has every kind equally."""
    out = []
    for i in range(n_per_kind * len(ALL_KINDS)):
        out.append(_spec_for_kind(ALL_KINDS[i % len(ALL_KINDS)], seed0 + i))
    return out


AnalyzeFn = Callable[[list[Event], Topology], list[Incident]]


def first_detection(events: list[Event], topology: Topology, injected_at: datetime,
                    analyze_fn: AnalyzeFn | None = None, step_s: float = 1.0) -> Optional[datetime]:
    """Replay analyze on events with ts < cut, cut = injected_at + k * step_s (event time),
    and return the first cut at which the engine reports any incident. None if never."""
    if analyze_fn is None:
        from app.engine.run import analyze as analyze_fn  # noqa: N813
    evs = sorted(events, key=lambda e: (e.ts, e.id))
    if not evs:
        return None
    tss = [e.ts for e in evs]
    last = tss[-1]
    k = 1
    while True:
        cut = injected_at + timedelta(seconds=k * step_s)
        cut_s = iso(cut)
        n = bisect.bisect_left(tss, cut_s)
        if n and analyze_fn(evs[:n], topology):
            return cut
        if cut_s > last:
            return None
        k += 1


def mttd_seconds(detected_at: Optional[datetime], injected_at: datetime) -> Optional[float]:
    if detected_at is None:
        return None
    return round((detected_at - injected_at).total_seconds(), 1)


def is_resolved(inc: Incident) -> bool:
    return inc.lifecycle == "RESOLVED" or inc.status == "RESOLVED"


def first_resolution(events: list[Event], topology: Topology, incident_id: str, after: datetime,
                     analyze_fn: AnalyzeFn | None = None, min_s: int = 10) -> Optional[datetime]:
    """First cut (after + k s, whole seconds, k >= min_s) at which the engine reports the
    incident RESOLVED on events with ts < cut. Binary search: once healed, the simulator
    stays healthy, so RESOLVED is monotonic in the cut. None if not resolved by the end."""
    if analyze_fn is None:
        from app.engine.run import analyze as analyze_fn  # noqa: N813
    evs = sorted(events, key=lambda e: (e.ts, e.id))
    if not evs:
        return None
    tss = [e.ts for e in evs]
    last = datetime.fromisoformat(tss[-1].replace("Z", "+00:00"))

    def resolved_at(k: int) -> bool:
        n = bisect.bisect_left(tss, iso(after + timedelta(seconds=k)))
        return any(i.id == incident_id and is_resolved(i) for i in analyze_fn(evs[:n], topology))

    hi = int((last - after).total_seconds()) + 1
    if hi < min_s or not resolved_at(hi):
        return None
    lo = min_s
    while lo < hi:
        mid = (lo + hi) // 2
        if resolved_at(mid):
            hi = mid
        else:
            lo = mid + 1
    return after + timedelta(seconds=lo)
