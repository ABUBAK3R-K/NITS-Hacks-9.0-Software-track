"""Synthetic scenario builders for engine tests.

Same event vocabulary as contracts/gen_fixtures.py (and Lane A's simulator):
metrics latency_p95_ms / error_rate every 2 s per service with staggered
phases (+ db_pool_in_use_pct and pg_lock_wait_ms on postgres), request
events (http_request, attrs.status/user_id, amount_inr/order_id on
payments-svc), logs, alerts ([FIRING] <Name>) and change events (deploy,
attrs.version). Numbers are randomised per seed and onset lags per hop are
300 to 1500 ms, so tests do not overfit to exact values.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.schemas import Event

T0 = datetime(2026, 11, 9, 9, 0, 0, tzinfo=timezone.utc)

BASE_LAT = {"api-gateway": 120, "auth-svc": 45, "payments-svc": 180, "ledger-svc": 60,
            "redis-session": 2, "postgres": 8, "psp-adapter": 350}
BASE_ERR = {"api-gateway": 0.004, "auth-svc": 0.003, "payments-svc": 0.006, "ledger-svc": 0.002,
            "redis-session": 0.0, "postgres": 0.0, "psp-adapter": 0.01}
DEPS = {"api-gateway": ["auth-svc", "payments-svc"], "auth-svc": ["redis-session", "postgres"],
        "payments-svc": ["ledger-svc", "psp-adapter"], "ledger-svc": ["postgres"]}


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Builder:
    def __init__(self, seed: int, start_id: int = 1):
        self.rng = random.Random(seed)
        self.n = start_id
        self.events: list[dict] = []

    def add(self, sec: float, service: str, kind: str, severity: str, name: str, value=None, message=None,
            **attrs) -> str:
        eid = f"evt_{self.n:06d}"
        self.n += 1
        ev = {"id": eid, "ts": iso(T0 + timedelta(seconds=sec)), "source": "sim", "service": service,
              "kind": kind, "severity": severity, "name": name, "attrs": attrs}
        if value is not None:
            ev["value"] = round(float(value), 4)
        if message is not None:
            ev["message"] = message
        self.events.append(ev)
        return eid

    def models(self) -> list[Event]:
        evs = sorted(self.events, key=lambda e: (e["ts"], e["id"]))
        return [Event.model_validate(e) for e in evs]


def metrics(b: Builder, seconds: float, elevated: dict[str, dict[str, float]], onset: dict[str, float],
            recover_at: float | None = None, silent: dict[str, float] | None = None) -> None:
    """Scrapes every 2 s per service, staggered phase, +-10% noise. From a
    service's onset its elevated metrics report the degraded value (+-8%)."""
    rng = b.rng
    silent = silent or {}
    for i, svc in enumerate(BASE_LAT):
        phase = 0.17 * i + rng.uniform(0, 0.05)
        k = 0
        while 2 * k + phase < seconds:
            t = 2 * k + phase
            k += 1
            if svc in silent and t >= silent[svc] and (recover_at is None or t < recover_at):
                continue
            bad = svc in onset and t >= onset[svc] and (recover_at is None or t < recover_at)
            vals = {"latency_p95_ms": BASE_LAT[svc] * rng.uniform(0.9, 1.1),
                    "error_rate": max(0.0, BASE_ERR[svc] * rng.uniform(0.5, 1.5))}
            if svc == "postgres":
                vals["db_pool_in_use_pct"] = rng.uniform(34, 48)
                vals["pg_lock_wait_ms"] = rng.uniform(1.5, 4.5)
            for name, v in vals.items():
                sev = "info"
                if bad and name in elevated.get(svc, {}):
                    v, sev = elevated[svc][name] * rng.uniform(0.92, 1.08), "error"
                    if name == "db_pool_in_use_pct":
                        v = 100.0
                attrs = {"pool_max": 100} if name == "db_pool_in_use_pct" else {}
                b.add(t, svc, "metric", sev, name, v, **attrs)


def requests(b: Builder, seconds: float, fail_from: dict[str, float], rate: float = 2.0,
             base_fail: float = 0.004, recover_at: float | None = None) -> None:
    rng = b.rng
    t, users = 0.0, [f"u_{n}" for n in range(1000, 1600)]
    while True:
        t += rng.expovariate(rate)
        if t >= seconds:
            break
        user = rng.choice(users)
        active = recover_at is None or t < recover_at
        if rng.random() < 0.4:
            bad = (active and t >= fail_from.get("auth-svc", 1e9) and rng.random() < 0.7) or rng.random() < base_fail
            b.add(t, "auth-svc", "request", "error" if bad else "info", "http_request",
                  message=f"POST /v1/login {503 if bad else 200}", user_id=user, status=503 if bad else 200,
                  route="/v1/login")
        else:
            amt = rng.choice([99, 149, 199, 299, 499, 799, 999, 1299, 2499, 4999])
            bad = (active and t >= fail_from.get("payments-svc", 1e9) and rng.random() < 0.75) or \
                rng.random() < base_fail
            b.add(t, "payments-svc", "request", "error" if bad else "info", "http_request",
                  message=f"POST /v1/payments {503 if bad else 201}", user_id=user, status=503 if bad else 201,
                  route="/v1/payments", amount_inr=amt, order_id=f"ord_{rng.randint(100000, 999999)}")


def propagate(b: Builder, root: str, t_root: float, among: set[str]) -> dict[str, float]:
    """Onset per service: callers in `among` fail 300 to 1500 ms after the dependency."""
    onset = {root: t_root}
    frontier = [root]
    while frontier:
        nxt = []
        for dep in frontier:
            for caller in sorted(DEPS):
                if dep in DEPS[caller] and caller in among and caller not in onset:
                    onset[caller] = onset[dep] + b.rng.uniform(0.3, 1.5)
                    nxt.append(caller)
        frontier = nxt
    return onset


def alerts(b: Builder, onset: dict[str, float], specs: list[tuple[str, str, str, float]]) -> None:
    for svc, name, sev, val in specs:
        if svc in onset:
            b.add(onset[svc] + b.rng.uniform(2.0, 9.0), svc, "alert", sev, name, val,
                  message=f"[FIRING] {name} on {svc}")


ONSET_LOGS = {
    "postgres": ("critical", "FATAL: remaining connection slots are reserved for non-replication superuser connections"),
    "auth-svc": ("error", "login failed: could not acquire DB connection within 2000ms"),
    "ledger-svc": ("error", "ledger write failed: timeout acquiring connection from pool (postgres:5432)"),
    "payments-svc": ("error", "POST /v1/payments -> ledger-svc 503: upstream unavailable"),
}


def scenario(kind: str | None, seed: int = 0, baseline_s: float = 40.0, after_s: float = 40.0,
             rate: float = 2.0, variant: str = "") -> list[Event]:
    """kind: None (steady state), db_pool, bad_deploy, redis_down, red_herring, psp_slow, ambiguous."""
    b = Builder(seed)
    rng = b.rng
    T = baseline_s
    total = baseline_s + after_s
    if kind is None:
        metrics(b, total, {}, {})
        requests(b, total, {}, rate=rate)
        # a few stray, harmless signals that must not page anyone
        for _ in range(3):
            svc = rng.choice(list(BASE_LAT))
            b.add(rng.uniform(5, total - 5), svc, "log", "error", "log", message="transient error: retrying once")
        b.add(rng.uniform(5, total - 5), "payments-svc", "change", "info", "deploy",
              message="payments-svc v2.2.9 -> v2.3.0", version="v2.3.0", previous_version="v2.2.9")
        return b.models()

    if kind in ("db_pool", "red_herring"):
        among = {"postgres", "auth-svc", "ledger-svc", "payments-svc", "api-gateway"}
        onset = propagate(b, "postgres", T, among)
        elevated = {"postgres": {"db_pool_in_use_pct": 100.0, "latency_p95_ms": 2400.0},
                    "auth-svc": {"error_rate": 0.31, "latency_p95_ms": 2110.0},
                    "ledger-svc": {"error_rate": 0.42, "latency_p95_ms": 2050.0},
                    "payments-svc": {"error_rate": 0.38, "latency_p95_ms": 2620.0},
                    "api-gateway": {"error_rate": 0.22, "latency_p95_ms": 3120.0}}
        metrics(b, total, elevated, onset)
        requests(b, total, {"auth-svc": onset["auth-svc"], "payments-svc": onset["payments-svc"]}, rate=rate)
        for svc, (sev, msg) in ONSET_LOGS.items():
            b.add(onset[svc] + rng.uniform(0.0, 0.3), svc, "log", sev, "log", message=msg)
        if kind == "red_herring":
            b.add(T - 30 + rng.uniform(-2, 2), "ledger-svc", "change", "info", "deploy",
                  message="ledger-svc v4.1.3 -> v4.1.4 (logging change)", version="v4.1.4",
                  previous_version="v4.1.3", actor="ci-bot")
        alerts(b, onset, [("api-gateway", "GatewayLatencyP95High", "critical", 3120.0),
                          ("auth-svc", "LoginFailureRateHigh", "critical", 0.31),
                          ("payments-svc", "PaymentFailureRateHigh", "critical", 0.38),
                          ("ledger-svc", "LedgerWriteErrors", "critical", 0.42),
                          ("postgres", "PgConnectionsSaturated", "critical", 100.0)])
        return b.models()

    if kind == "bad_deploy":
        b.add(T - rng.uniform(20, 90), "payments-svc", "change", "info", "deploy",
              message="payments-svc v2.3.0 -> v2.3.1", version="v2.3.1", previous_version="v2.3.0", actor="ci-bot")
        onset = propagate(b, "payments-svc", T, {"payments-svc", "api-gateway"})
        metrics(b, total, {"payments-svc": {"error_rate": 0.45},
                           "api-gateway": {"error_rate": 0.2}}, onset)
        requests(b, total, {"payments-svc": onset["payments-svc"]}, rate=rate)
        b.add(T + rng.uniform(0, 0.3), "payments-svc", "log", "error", "log",
              message="NullPointerException in PaymentRouter.route (v2.3.1)")
        alerts(b, onset, [("payments-svc", "PaymentFailureRateHigh", "critical", 0.45),
                          ("api-gateway", "Gateway5xxRateHigh", "error", 0.2)])
        return b.models()

    if kind == "redis_down":
        onset = propagate(b, "redis-session", T, {"redis-session", "auth-svc", "api-gateway"})
        silent = {"redis-session": T} if variant == "silent" else None
        metrics(b, total, {"redis-session": {"error_rate": 1.0, "latency_p95_ms": 1000.0},
                           "auth-svc": {"error_rate": 0.6, "latency_p95_ms": 900.0},
                           "api-gateway": {"error_rate": 0.25}}, onset, silent=silent)
        requests(b, total, {"auth-svc": onset["auth-svc"]}, rate=rate)
        b.add(onset["auth-svc"] + 0.05, "auth-svc", "log", "error", "log",
              message="session lookup failed: redis-session:6379 connection refused")
        alerts(b, onset, [("auth-svc", "LoginFailureRateHigh", "critical", 0.6),
                          ("api-gateway", "Gateway5xxRateHigh", "error", 0.25)])
        return b.models()

    if kind == "psp_slow":
        onset = propagate(b, "psp-adapter", T, {"psp-adapter", "payments-svc", "api-gateway"})
        metrics(b, total, {"psp-adapter": {"latency_p95_ms": 6200.0},
                           "payments-svc": {"latency_p95_ms": 6600.0, "error_rate": 0.12},
                           "api-gateway": {"latency_p95_ms": 6900.0}}, onset)
        requests(b, total, {"payments-svc": onset["payments-svc"] + 3.0}, rate=rate)
        alerts(b, onset, [("payments-svc", "PaymentsLatencyP95High", "warn", 6600.0),
                          ("psp-adapter", "PspLatencyHigh", "critical", 6200.0)])
        return b.models()

    if kind == "ambiguous":
        b.add(T - 31 + rng.uniform(-1, 1), "ledger-svc", "change", "info", "deploy",
              message="ledger-svc v4.1.9 -> v4.2.0 (migration 0042_add_settlement_idx)", version="v4.2.0",
              previous_version="v4.1.9", actor="ci-bot")
        onset = propagate(b, "ledger-svc", T, {"ledger-svc", "payments-svc", "api-gateway"})
        # postgres lock waits cross the threshold last, more than one 2 s scrape after the gateway, so the
        # observed order is ledger first and postgres last (the honest "cannot tell yet" case)
        onset["postgres"] = max(onset.values()) + rng.uniform(2.1, 3.0)
        metrics(b, total, {"ledger-svc": {"error_rate": 0.47}, "payments-svc": {"error_rate": 0.29},
                           "api-gateway": {"latency_p95_ms": 1480.0}, "postgres": {"pg_lock_wait_ms": 870.0}},
                onset)
        requests(b, total, {"payments-svc": onset["payments-svc"]}, rate=rate)
        b.add(T, "ledger-svc", "log", "error", "log",
              message="ledger write failed: canceling statement due to lock timeout (relation ledger_entries)")
        alerts(b, onset, [("payments-svc", "PaymentFailureRateHigh", "critical", 0.29),
                          ("ledger-svc", "LedgerWriteErrors", "critical", 0.47),
                          ("postgres", "PgLockWaitHigh", "warn", 870.0)])
        return b.models()
    raise ValueError(kind)
