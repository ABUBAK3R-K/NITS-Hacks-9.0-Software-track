"""In-memory event store + incident cache + SSE fan-out. No DB.

Owned by Lane A. Lane B's engine is called on recompute through
`app.engine.run.analyze(events, topology)`, looked up at call time so tests
can monkeypatch it (or pass `analyze_fn=` to Store).

The demo must never break: any exception from analyze is logged and the
previous incidents are kept.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from app.schemas import (
    ApplyFixResult, Event, FaultSpec, FaultType, Incident, IncidentSummary, Run, RunReveal, ServiceHealth, SimStatus,
    Topology, TopologyState,
)
from app.sim.generator import Simulator, iso, utcnow
from app.sim.faults import ActiveFault, fix_matches
from app.sim.runner import first_detection, first_resolution, is_resolved, mttd_seconds, start_spec
from app.sim.topology import deps_map, load_topology

log = logging.getLogger("jarvis.store")

MAX_EVENTS = 50_000
WINDOW_S = 15 * 60
DEBOUNCE_S = 1.0
SSE_EVENTS_PER_S = 20
BACKFILL_S = 60.0  # baseline history generated instantly on sim start
QUEUE_MAX = 2000
MAX_RUNS = 50
INCIDENT_LOOKBACK_S = 300.0  # events_for_incident: window starts this long before opened_at
RUN_AFTER_FIX_S = 90.0  # sandbox: simulated time generated after a fix, so the engine can see recovery

AnalyzeFn = Callable[[list[Event], Topology], list[Incident]]


def _default_analyze(events: list[Event], topology: Topology) -> list[Incident]:
    from app.engine import run  # looked up per call so monkeypatching run.analyze works
    return run.analyze(events, topology)


def _priority(e: Event) -> int:
    if e.kind == "alert":
        return 0
    if e.kind == "change":
        return 1
    if e.severity != "info":
        return 2
    return 3


def event_json(e: Event) -> str:
    return e.model_dump_json(exclude_none=True)


def incident_json(inc: Incident) -> str:
    return inc.model_dump_json(by_alias=True)


def summarize(inc: Incident) -> IncidentSummary:
    rc = inc.root_cause
    conf = rc.confidence if rc else (inc.candidates[0].score if inc.candidates else None)
    return IncidentSummary(
        id=inc.id, title=inc.title, status=inc.status, opened_at=inc.opened_at,
        updated_at=inc.updated_at, services=inc.services, alert_count=inc.alert_count,
        severity=inc.severity, root_service=rc.service if rc else None, confidence=conf)


def build_topology_state(incidents: Iterable[Incident], topology: Topology, ts: str) -> TopologyState:
    """Services in non-resolved incidents are degraded, critical when they are
    the root cause or carry a critical evidence event, healthy otherwise.
    hot_edges walk from each root up through the callers inside its incident."""
    live = [i for i in incidents if i.status != "RESOLVED"]
    roots = {i.root_cause.service for i in live if i.root_cause}
    health: dict[str, str] = {}
    for inc in live:
        root = inc.root_cause.service if inc.root_cause else None
        crit = {e.service for e in inc.evidence_events if e.severity == "critical"}
        for svc in inc.services:
            h = "critical" if svc == root or svc in crit else "degraded"
            if health.get(svc) != "critical":
                health[svc] = h
    services = [ServiceHealth(service=s.id, health=health.get(s.id, "healthy"), is_root_cause=s.id in roots)
                for s in topology.services]
    deps = deps_map()
    hot: list[tuple[str, str]] = []
    for inc in live:
        if not inc.root_cause:
            continue
        frontier = {inc.root_cause.service}
        while frontier:
            nxt = set()
            for caller, callees in deps.items():
                for d in callees:
                    if d in frontier and caller in inc.services and (caller, d) not in hot:
                        hot.append((caller, d))
                        nxt.add(caller)
            frontier = nxt
    return TopologyState(ts=ts, services=services, hot_edges=hot)


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _shift(ts: str, seconds: float) -> Optional[str]:
    t = _parse(ts)
    return None if t is None else iso(t + timedelta(seconds=seconds))


def incident_window(events: Iterable[Event], inc: Incident) -> list[Event]:
    """Events from INCIDENT_LOOKBACK_S before the incident opened until it was last updated."""
    lo = _shift(inc.opened_at, -INCIDENT_LOOKBACK_S) or inc.opened_at
    hi = _shift(inc.updated_at, 1.0) or inc.updated_at
    return sorted((e for e in events if lo <= e.ts <= hi), key=lambda e: (e.ts, e.id))


# ---------------------------------------------------------------- sandbox runs (U1)
@dataclass
class RunRecord:
    run: Run
    spec: FaultSpec
    events: list[Event]
    injected_at: datetime
    sim: Optional[Simulator] = None  # kept so apply_fix can continue the run
    fault: Optional[ActiveFault] = None


def _primary(incidents: list[Incident]) -> Incident:
    """The run's answer: the incident spanning the most services (ties: earliest).
    A fault gives one incident on our simulator; this only matters if the engine split it."""
    return sorted(incidents, key=lambda i: (-len(i.services), i.opened_at, i.id))[0]


class RunStore:
    """Isolated sandbox runs keyed by run_id, LRU-capped at MAX_RUNS. Each run owns its
    event list; nothing here touches the live store, so judges never collide."""

    def __init__(self, analyze_fn: AnalyzeFn, topology: Topology, max_runs: int = MAX_RUNS):
        self.analyze_fn = analyze_fn
        self.topology = topology
        self.max_runs = max_runs
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._lock = threading.Lock()

    def create(self, spec: FaultSpec, mode: str, now: Optional[datetime] = None) -> Run:
        """Simulate spec fast-forwarded (no sleeping) so its events end at `now`, run the
        engine on the whole run, and fill mttd_s by prefix replay (outside the engine)."""
        now = (now or utcnow()).replace(microsecond=0)
        baseline_s, after_s = 60.0, 90.0
        start = now - timedelta(seconds=baseline_s + spec.onset_jitter_s + after_s)
        sim, fault, events = start_spec(spec, baseline_s=baseline_s, after_s=after_s, start=start)
        injected_at = fault.injected_at or fault.start
        incidents = list(self.analyze_fn(events, self.topology)) if events else []
        if incidents:
            detected = first_detection(events, self.topology, injected_at, analyze_fn=self.analyze_fn)
            mttd = mttd_seconds(detected, injected_at)
            if mttd is not None:
                incidents = [i.model_copy(update={"mttd_s": mttd}) for i in incidents]
        hidden = mode == "blind"
        run = Run(run_id=f"run_{uuid.uuid4().hex[:10]}", mode=mode, created_at=iso(now),
                  spec_hidden=hidden, spec=None if hidden else spec, incidents=incidents)
        with self._lock:
            self._runs[run.run_id] = RunRecord(run=run, spec=spec, events=events, injected_at=injected_at,
                                               sim=sim, fault=fault)
            while len(self._runs) > self.max_runs:
                self._runs.popitem(last=False)
        return run

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is not None:
                self._runs.move_to_end(run_id)
            return rec

    def reveal(self, run_id: str) -> Optional[RunReveal]:
        rec = self.get(run_id)
        if rec is None:
            return None
        rec.run = rec.run.model_copy(update={"spec_hidden": False, "spec": rec.spec})
        top1, abstained = None, False
        if rec.run.incidents:
            inc = _primary(rec.run.incidents)
            abstained = inc.status == "ABSTAINED"
            if inc.root_cause is not None:
                top1 = inc.root_cause.service
            elif inc.candidates:
                top1 = inc.candidates[0].service
        return RunReveal(run_id=run_id, spec=rec.spec, expected_root=rec.spec.target, our_top1=top1,
                         abstained=abstained, matched=top1 == rec.spec.target and not abstained)

    def apply_fix(self, rec: RunRecord, incident_id: str, fix_id: str) -> ApplyFixResult:
        """U5 in a sandbox: apply the fix at the run's current sim time, fast-forward
        RUN_AFTER_FIX_S more, re-run the engine. The lifecycle comes only from the engine;
        mttr_s = first replay cut where the engine says RESOLVED, minus injection."""
        f, sim = rec.fault, rec.sim
        if f is None or sim is None:
            return ApplyFixResult(incident_id=incident_id, fix_id=fix_id, applied=False,
                                  message="this run cannot be continued")
        if f.fixed_at is not None:
            return ApplyFixResult(incident_id=incident_id, fix_id=fix_id, applied=False,
                                  message=f"a fix ({f.fix_id}) was already applied in this run")
        if not fix_matches(rec.spec, fix_id):
            return ApplyFixResult(incident_id=incident_id, fix_id=fix_id, applied=False,
                                  message=f"{fix_id} does not remediate the active fault; nothing changed")
        fixed_at = sim.t
        sim.apply_fix(f, fix_id, fixed_at)
        more = sim.advance(fixed_at + timedelta(seconds=RUN_AFTER_FIX_S))
        events = rec.events + more
        incidents = list(self.analyze_fn(events, self.topology))
        old = {i.id: i for i in rec.run.incidents}
        resolved = first_resolution(events, self.topology, incident_id, fixed_at, analyze_fn=self.analyze_fn)
        out = []
        for inc in incidents:
            upd = {}
            if inc.id in old and old[inc.id].mttd_s is not None:
                upd["mttd_s"] = old[inc.id].mttd_s
            if inc.id == incident_id and resolved is not None and is_resolved(inc):
                upd["mttr_s"] = mttd_seconds(resolved, rec.injected_at)
            out.append(inc.model_copy(update=upd) if upd else inc)
        with self._lock:
            rec.events = events
            rec.run = rec.run.model_copy(update={"incidents": out})
        return ApplyFixResult(incident_id=incident_id, fix_id=fix_id, applied=True,
                              message=f"{fix_id} applied to {f.spec.root}; simulated {RUN_AFTER_FIX_S:.0f} s of recovery")

    def find_incident(self, incident_id: str) -> Optional[tuple[RunRecord, Incident]]:
        with self._lock:
            recs = list(self._runs.values())
        for rec in reversed(recs):  # most recent first
            for inc in rec.run.incidents:
                if inc.id == incident_id:
                    return rec, inc
        return None

    def __len__(self) -> int:
        return len(self._runs)


class Store:
    def __init__(self, analyze_fn: Optional[AnalyzeFn] = None, topology: Optional[Topology] = None,
                 max_events: int = MAX_EVENTS, window_s: float = WINDOW_S, seed: int = 7):
        self.analyze_fn = analyze_fn or _default_analyze
        self.topology = topology or load_topology()
        self.max_events = max_events
        self.window_s = window_s
        self.seed = seed
        self.events: deque[Event] = deque()
        self.ids: set[str] = set()
        self.incidents: dict[str, Incident] = {}
        self._inc_json: dict[str, str] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.dirty = False
        self._last_recompute = 0.0
        self._recomputing = False
        self._budget_sec = 0
        self._budget_used = 0
        self.last_error: Optional[str] = None
        self.sim: Optional[Simulator] = None
        self.running = False
        self._next_sim_id = 1
        self._upload_id = 0
        self._gen = 0  # bumped by reset, so a recompute started before reset is discarded
        # Live MTTD: each live fault's injection time and the event time of the first
        # recompute that produced an incident opening at or after it.
        self._injections: list[list[Optional[datetime]]] = []  # [injected_at, detected_at]
        self._resolved_at: dict[str, datetime] = {}  # incident id -> first recompute showing RESOLVED
        self.runs = RunStore(lambda ev, topo: self.analyze_fn(ev, topo), self.topology)

    # ------------------------------------------------------------ events
    def add_events(self, events: Iterable[Event]) -> tuple[int, list[str]]:
        """Append events, skipping duplicate ids. Returns (accepted, errors)."""
        accepted, errors, fresh = 0, [], []
        for e in events:
            if e.id in self.ids:
                errors.append(f"{e.id}: duplicate id")
                continue
            self.events.append(e)
            self.ids.add(e.id)
            fresh.append(e)
            accepted += 1
        self._evict()
        if fresh:
            self.dirty = True
            self._publish_events(fresh)
        return accepted, errors

    def _evict(self) -> None:
        while len(self.events) > self.max_events:
            self.ids.discard(self.events.popleft().id)
        if not self.events:
            return
        tail = [self.events[i].ts for i in range(max(0, len(self.events) - 200), len(self.events))]
        cutoff = _shift(max(tail), -self.window_s)
        if cutoff is None:
            return
        # Age eviction only drops sim events, so uploaded history is kept.
        while self.events and self.events[0].source == "sim" and self.events[0].ts < cutoff:
            self.ids.discard(self.events.popleft().id)

    def snapshot(self) -> list[Event]:
        return sorted(self.events, key=lambda e: (e.ts, e.id))

    def new_upload_id(self) -> str:
        self._upload_id += 1
        return f"evt_up{self._upload_id:06d}"

    # ------------------------------------------------------------ incidents
    def recompute(self, force: bool = False) -> bool:
        """Synchronous recompute (tests, ingest). Debounced to once per second."""
        if not self._should_recompute(force):
            return False
        self._last_recompute = time.monotonic()
        self.dirty = False
        self._apply(self._run_analyze(self.snapshot()))
        return True

    async def recompute_async(self, force: bool = False) -> bool:
        """Same as recompute, but analyze runs in a worker thread so the event
        loop (SSE, HTTP) never blocks on the engine."""
        if self._recomputing or not self._should_recompute(force):
            return False
        self._recomputing = True
        try:
            self._last_recompute = time.monotonic()
            self.dirty = False
            gen = self._gen
            result = await asyncio.to_thread(self._run_analyze, self.snapshot())
            if gen == self._gen:
                self._apply(result)
        finally:
            self._recomputing = False
        return True

    def _should_recompute(self, force: bool) -> bool:
        if force:
            return True
        return self.dirty and time.monotonic() - self._last_recompute >= DEBOUNCE_S

    def _run_analyze(self, events: list[Event]) -> Optional[list[Incident]]:
        try:
            out = self.analyze_fn(events, self.topology)
            return [i if isinstance(i, Incident) else Incident.model_validate(i) for i in out]
        except Exception as exc:  # noqa: BLE001  the demo must never break
            err = f"{type(exc).__name__}: {exc}"
            if err != self.last_error:  # log each distinct failure once, not every second
                log.warning("analyze failed, keeping previous incidents: %s", err, exc_info=True)
            self.last_error = err
            return None

    def _latest_event_time(self) -> Optional[datetime]:
        if not self.events:
            return None
        n = len(self.events)
        return _parse(max(self.events[i].ts for i in range(max(0, n - 200), n)))

    def _fill_mttd(self, result: list[Incident]) -> list[Incident]:
        """mttd_s = event time of the first recompute that showed an incident for a live
        fault, minus that fault's injection time. Computed here, never in the engine."""
        if not self._injections:
            return result
        now = self._latest_event_time()
        out = []
        for inc in result:
            opened = _parse(inc.opened_at)
            slot = None
            if opened is not None and now is not None:
                for s in self._injections:  # latest injection at or before the incident's onset
                    if s[0] - timedelta(seconds=1) <= opened:
                        slot = s
            if slot is not None:
                if slot[1] is None:
                    slot[1] = max(now, slot[0])
                upd = {"mttd_s": round((slot[1] - slot[0]).total_seconds(), 1)}
                if is_resolved(inc):  # mttr only from the engine's own RESOLVED verdict
                    r = self._resolved_at.setdefault(inc.id, max(now, slot[0]))
                    upd["mttr_s"] = round((r - slot[0]).total_seconds(), 1)
                inc = inc.model_copy(update=upd)
            out.append(inc)
        return out

    def _apply(self, result: Optional[list[Incident]]) -> None:
        if result is not None:
            result = self._fill_mttd(result)
            new = {i.id: i for i in result}
            new_json = {i.id: incident_json(i) for i in result}
            changed = [k for k in new if self._inc_json.get(k) != new_json[k]]
            self.incidents, self._inc_json = new, new_json
            for k in changed:
                self.publish("incident_update", new_json[k])
        self.publish_topology()

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Live incidents first, then every sandbox run."""
        inc = self.incidents.get(incident_id)
        if inc is not None:
            return inc
        hit = self.runs.find_incident(incident_id)
        return hit[1] if hit else None

    def events_for_incident(self, incident_id: str) -> list[Event]:
        """Events in the incident's window, from the live store or its run namespace."""
        inc = self.incidents.get(incident_id)
        if inc is not None:
            return incident_window(self.snapshot(), inc)
        hit = self.runs.find_incident(incident_id)
        if hit is None:
            return []
        rec, inc = hit
        return incident_window(rec.events, inc)

    def apply_fix(self, incident_id: str, fix_id: str) -> Optional[ApplyFixResult]:
        """U5: remediate a live or sandbox incident. None if the incident is unknown.
        Never touches lifecycle: the engine moves it once it sees the fix_applied event
        and the metrics back within baseline."""
        inc = self.incidents.get(incident_id)
        if inc is None:
            hit = self.runs.find_incident(incident_id)
            if hit is None:
                return None
            return self.runs.apply_fix(hit[0], incident_id, fix_id)
        faults = [f for f in (self.sim.faults if self.sim else [])
                  if f.fixed_at is None and f.fspec is not None and f.spec.root in inc.services
                  and fix_matches(f.fspec, fix_id)]
        if not faults:
            return ApplyFixResult(incident_id=incident_id, fix_id=fix_id, applied=False,
                                  message=f"no active fault in this incident is remediated by {fix_id}; nothing changed")
        for f in faults:
            self.sim.apply_fix(f, fix_id)
        roots = ", ".join(sorted({f.spec.root for f in faults}))
        return ApplyFixResult(incident_id=incident_id, fix_id=fix_id, applied=True,
                              message=f"{fix_id} applied to {roots}; recovery takes about 15 s")

    def summaries(self) -> list[IncidentSummary]:
        return [summarize(i) for i in self.incidents.values()]

    def topology_state(self) -> TopologyState:
        return build_topology_state(self.incidents.values(), self.topology, iso(utcnow()))

    # ------------------------------------------------------------ SSE fan-out
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, name: str, data: str) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait((name, data))
            except asyncio.QueueFull:
                pass  # slow client: drop rather than block the producer

    def publish_topology(self) -> None:
        if self.subscribers:
            self.publish("topology_state", self.topology_state().model_dump_json(by_alias=True))

    def _publish_events(self, events: list[Event]) -> None:
        """Throttled to about SSE_EVENTS_PER_S, preferring alerts, changes and non-info."""
        if not self.subscribers:
            return
        sec = int(time.monotonic())
        if sec != self._budget_sec:
            self._budget_sec, self._budget_used = sec, 0
        room = SSE_EVENTS_PER_S - self._budget_used
        if room <= 0:
            return
        picked = sorted(events, key=_priority)[:room]
        self._budget_used += len(picked)
        for e in sorted(picked, key=lambda e: (e.ts, e.id)):
            self.publish("event", event_json(e))

    # ------------------------------------------------------------ simulator
    def status(self) -> SimStatus:
        faults = self.sim.active_faults if self.sim else []
        return SimStatus(running=self.running, active_faults=faults, events_in_store=len(self.events))

    def start(self) -> None:
        if self.running:
            return
        now = utcnow()
        self.sim = Simulator(now - timedelta(seconds=BACKFILL_S), seed=self.seed, id_start=self._next_sim_id)
        self.running = True
        self.add_events(self.sim.advance(now))
        self._next_sim_id = self.sim.next_id

    def inject(self, ftype: FaultType) -> None:
        if not self.running:
            self.start()
        assert self.sim is not None
        if ftype not in self.sim.active_faults:
            self.sim.inject(ftype)
            f = self.sim.faults[-1]
            self._injections.append([f.injected_at or f.start, None])

    def tick(self, now: Optional[datetime] = None) -> int:
        """Advance the live simulator to `now` (real clock). Returns events added."""
        if not self.running or self.sim is None:
            return 0
        evs = self.sim.advance(now or utcnow())
        self._next_sim_id = self.sim.next_id
        accepted, _ = self.add_events(evs)
        return accepted

    def reset(self) -> None:
        """Clear faults, events and incidents. A running sim keeps running on a fresh baseline."""
        was_running = self.running
        self._gen += 1
        self.events.clear()
        self.ids.clear()
        self.incidents.clear()
        self._inc_json.clear()
        self._injections.clear()
        self._resolved_at.clear()
        self.dirty = False
        self.last_error = None
        self.running = False
        self.sim = None
        if was_running:
            self.start()
        self.publish_topology()


store = Store()


async def run_loop(st: Store, tick_s: float = 0.5, heartbeat_s: float = 2.0) -> None:
    """Background task: live sim ticks, debounced recompute, 2 s topology heartbeat."""
    last_hb = 0.0
    while True:
        await asyncio.sleep(tick_s)
        try:
            st.tick()
            recomputed = await st.recompute_async()
            now = time.monotonic()
            if recomputed:
                last_hb = now  # recompute already published topology_state
            elif now - last_hb >= heartbeat_s:
                st.publish_topology()
                last_hb = now
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("sim loop iteration failed")
