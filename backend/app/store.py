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
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from app.schemas import (
    Event, FaultType, Incident, IncidentSummary, ServiceHealth, SimStatus, Topology, TopologyState,
)
from app.sim.generator import Simulator, iso, utcnow
from app.sim.topology import deps_map, load_topology

log = logging.getLogger("jarvis.store")

MAX_EVENTS = 50_000
WINDOW_S = 15 * 60
DEBOUNCE_S = 1.0
SSE_EVENTS_PER_S = 20
BACKFILL_S = 60.0  # baseline history generated instantly on sim start
QUEUE_MAX = 2000

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


def _shift(ts: str, seconds: float) -> Optional[str]:
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return iso(t + timedelta(seconds=seconds))


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

    def _apply(self, result: Optional[list[Incident]]) -> None:
        if result is not None:
            new = {i.id: i for i in result}
            new_json = {i.id: incident_json(i) for i in result}
            changed = [k for k in new if self._inc_json.get(k) != new_json[k]]
            self.incidents, self._inc_json = new, new_json
            for k in changed:
                self.publish("incident_update", new_json[k])
        self.publish_topology()

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
