"""Steady traffic for the simulated payments platform, plus active faults.

Vocabulary matches contracts/gen_fixtures.py:
  metrics  latency_p95_ms, error_rate every 2 s per service (staggered phase),
           plus db_pool_in_use_pct (attrs.pool_max=100) and pg_lock_wait_ms on postgres
  requests http_request on auth-svc (/v1/login) and payments-svc (/v1/payments,
           with amount_inr and order_id), attrs.status / user_id / route
  logs     kind=log, name "log", text in message
  alerts   kind=alert, name like GatewayLatencyP95High, message "[FIRING] <name> on <svc>"
  changes  kind=change, name "deploy", attrs.version

`Simulator.advance(until)` generates every event with ts < until without
sleeping. `simulate(...)` uses it offline; the live loop in store/main calls
it with the real clock.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.schemas import Event, FaultSpec, FaultType
from app.sim.faults import (
    ActiveFault, apply_fix, build_fault, build_spec_fault, draft, failure, fault_drafts, metric_override,
)
from app.sim.topology import service_ids

SCRAPE_S = 2.0
PHASE_S = 0.17
REQUEST_RATE = 2.0  # requests per second (logins + payments)
INFO_LOG_RATE = 0.7  # baseline info logs per second

BASE_LAT = {"api-gateway": 120, "auth-svc": 45, "payments-svc": 180, "ledger-svc": 60,
            "redis-session": 2, "postgres": 8, "psp-adapter": 350}
BASE_ERR = {"api-gateway": 0.004, "auth-svc": 0.003, "payments-svc": 0.006, "ledger-svc": 0.002,
            "redis-session": 0.0, "postgres": 0.0, "psp-adapter": 0.01}
AMOUNTS = [99, 149, 199, 299, 499, 799, 999, 1299, 2499, 4999]

INFO_LOGS = {
    "api-gateway": ["GET /v1/session 200 14ms", "POST /v1/payments 201 191ms upstream=payments-svc",
                    "POST /v1/login 200 52ms upstream=auth-svc"],
    "auth-svc": ["login ok user={u} in 41ms", "token refreshed user={u}", "session created user={u} ttl=1800s"],
    "payments-svc": ["payment captured order={o} psp=hdfc", "payment intent created order={o}"],
    "ledger-svc": ["ledger entry committed order={o}", "settlement batch 2026-11-09 progress 41%"],
    "redis-session": ["DB saved on disk", "Background saving started by pid 4121"],
    "postgres": ["LOG: checkpoint complete: wrote 312 buffers (1.9%)",
                 'LOG: automatic vacuum of table "payments.public.ledger_entries": index scans: 1'],
    "psp-adapter": ["bank callback received status=CAPTURED ref=HDFC{o}", "bank charge ok in 342ms"],
}


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Simulator:
    """Deterministic event generator. Same seed and same advance() calls give
    the same events."""

    def __init__(self, start: datetime, seed: int = 7, id_start: int = 1):
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self.rng = random.Random(seed)
        self.t = start
        self.next_id = id_start
        self.faults: list[ActiveFault] = []
        self.services = service_ids()
        self.next_scrape = {s: start + timedelta(seconds=PHASE_S * i) for i, s in enumerate(self.services)}
        self.next_req = start + timedelta(seconds=self.rng.expovariate(REQUEST_RATE))
        self.next_info = start + timedelta(seconds=self.rng.expovariate(INFO_LOG_RATE))
        self.users = [f"u_{n}" for n in range(1000, 1600)]

    @property
    def active_faults(self) -> list[FaultType]:
        return [f.type for f in self.faults if f.fixed_at is None]

    def apply_fix(self, f: ActiveFault, fix_id: str, at: datetime | None = None) -> None:
        """Remediate an active fault at `at` (default: now in sim time)."""
        apply_fix(f, fix_id, max(at or self.t, self.t), self.rng)

    def inject(self, ftype: FaultType, at: datetime | None = None) -> None:
        at = max(at or self.t, self.t)
        self.faults.append(build_fault(ftype, at, self.rng))

    def inject_spec(self, spec: FaultSpec, at: datetime | None = None, label: str = "custom",
                    past_ok: bool = False) -> ActiveFault:
        """Inject a FaultSpec primitive. past_ok lets the decoy deploy precede `at`
        (offline runs, where nothing has been generated yet)."""
        at = max(at or self.t, self.t)
        f = build_spec_fault(spec, at, self.rng, label=label, past_ok=past_ok, not_before=self.t)
        self.faults.append(f)
        return f

    def clear_faults(self) -> None:
        self.faults.clear()

    # ------------------------------------------------------------ generation
    def advance(self, until: datetime) -> list[Event]:
        """All events with self.t <= ts < until, sorted by ts, ids in ts order."""
        if until <= self.t:
            return []
        drafts: list[tuple[datetime, dict]] = []
        rng = self.rng
        for svc in self.services:
            while self.next_scrape[svc] < until:
                drafts.extend(self._scrape(svc, self.next_scrape[svc]))
                self.next_scrape[svc] += timedelta(seconds=SCRAPE_S)
        while self.next_req < until:
            drafts.append((self.next_req, self._request(self.next_req)))
            self.next_req += timedelta(seconds=rng.expovariate(REQUEST_RATE))
        while self.next_info < until:
            svc = self.services[rng.randrange(len(self.services))]
            tmpl = INFO_LOGS[svc][rng.randrange(len(INFO_LOGS[svc]))]
            msg = tmpl.format(u=rng.choice(self.users), o=f"ord_{rng.randint(100000, 999999)}")
            drafts.append((self.next_info, draft(svc, "log", "info", "log", message=msg)))
            self.next_info += timedelta(seconds=rng.expovariate(INFO_LOG_RATE))
        for f in self.faults:
            drafts.extend(fault_drafts(f, until, rng))
        self.t = until
        drafts.sort(key=lambda x: x[0])  # stable: equal ts keep generation order
        out = []
        for ts, d in drafts:
            out.append(self._event(ts, d))
        return out

    def _event(self, ts: datetime, d: dict) -> Event:
        eid = f"evt_{self.next_id:06d}"
        self.next_id += 1
        value = d["value"]
        return Event(id=eid, ts=iso(ts), source="sim", service=d["service"], kind=d["kind"],
                     severity=d["severity"], name=d["name"],
                     value=None if value is None else round(float(value), 4),
                     message=d["message"], attrs=d["attrs"])

    def _scrape(self, svc: str, t: datetime) -> list[tuple[datetime, dict]]:
        rng = self.rng
        vals = {"latency_p95_ms": BASE_LAT[svc] * rng.uniform(0.9, 1.12),
                "error_rate": max(0.0, BASE_ERR[svc] * rng.uniform(0.5, 1.5))}
        if svc == "postgres":
            vals["db_pool_in_use_pct"] = rng.uniform(34, 48)
            vals["pg_lock_wait_ms"] = rng.uniform(1.5, 4.5)
        out = []
        for name, v in vals.items():
            sev = "info"
            bad = metric_override(self.faults, svc, name, t)
            if bad:
                base_v = v
                v, sev = bad[0] * rng.uniform(0.92, 1.08), bad[1]
                if name == "db_pool_in_use_pct":
                    v = 100.0
                if name == "error_rate":
                    v = min(v, 1.0)
                frac = bad[2]
                if frac > 0:  # remediation: ease linearly back to this scrape's baseline value
                    v = v + (base_v - v) * frac
                    sev = "warn" if frac < 0.5 else "info"
            attrs = {"pool_max": 100} if name == "db_pool_in_use_pct" else {}
            out.append((t, draft(svc, "metric", sev, name, v, **attrs)))
        return out

    def _request(self, t: datetime) -> dict:
        rng = self.rng
        user = rng.choice(self.users)
        if rng.random() < 0.4:
            p, code = failure(self.faults, "auth-svc", t)
            bad = rng.random() < p
            status = code if bad else 200
            return draft("auth-svc", "request", "error" if bad else "info", "http_request",
                         message=f"POST /v1/login {status}", user_id=user, status=status, route="/v1/login")
        amt = rng.choice(AMOUNTS)
        order = f"ord_{rng.randint(100000, 999999)}"
        p, code = failure(self.faults, "payments-svc", t)
        bad = rng.random() < p
        status = code if bad else 201
        return draft("payments-svc", "request", "error" if bad else "info", "http_request",
                     message=f"POST /v1/payments {status}", user_id=user, status=status,
                     route="/v1/payments", amount_inr=amt, order_id=order)


DEFAULT_START = datetime(2026, 11, 9, 8, 30, 0, tzinfo=timezone.utc)  # 14:00 IST


def simulate(seconds: float, faults: list[tuple[float, FaultType]] | None = None,
             start: datetime = DEFAULT_START, seed: int = 7) -> list[Event]:
    """Pure offline timeline: `seconds` of traffic from `start`, with each
    (offset_seconds, fault_type) injected at start + offset. No sleeping,
    deterministic for a fixed seed. Events are sorted by ts, ids ascending."""
    sim = Simulator(start, seed=seed)
    out: list[Event] = []
    for off, ftype in sorted(faults or [], key=lambda x: x[0]):
        out += sim.advance(start + timedelta(seconds=off))
        sim.inject(ftype, start + timedelta(seconds=off))
    out += sim.advance(start + timedelta(seconds=seconds))
    return out
