"""Step 7: recommendations from a rule table keyed by (root tier, signature).

Every recommendation carries evidence ids from the incident. Templates use
{svc}, {ver}, {prev}. The LLM may rephrase these, never invent new ones.
"""
from __future__ import annotations

from typing import Optional

from app.schemas import Recommendation

from .common import Episode, TopoIndex
from .rca import Scored, Verdict

# (tier, signature) -> list of (action, why, runbook)
RULES: dict[tuple[str, str], list[tuple[str, str, str]]] = {
    ("data", "connection_pool_exhausted"): [
        ("Free {svc} connections: terminate idle-in-transaction sessions and add max_connections headroom",
         "{svc} hit its connection limit and refused new clients before its dependents failed",
         "psql -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' "
         "AND state_change < now() - interval '30 seconds';\" then confirm db_pool_in_use_pct drops below 80%. "
         "Raise max_connections or the PgBouncer pool_size only if usage stays high."),
        ("Cap per-client pool size in {callers} and fail fast with a 2 s acquire timeout",
         "the callers block on pool acquisition, which turns one saturated database into a platform outage",
         "Set DB_POOL_MAX=20 and DB_ACQUIRE_TIMEOUT_MS=2000 in each caller's config, then do a rolling restart "
         "one replica at a time."),
    ],
    ("data", "latency_spike"): [
        ("Find and stop the blocking session or slow query on {svc}",
         "{svc} latency or lock wait rose before its callers degraded",
         "psql -c \"SELECT pid, application_name, wait_event_type, state, query FROM pg_stat_activity "
         "WHERE wait_event_type IS NOT NULL ORDER BY query_start;\" and pg_cancel_backend(pid) the blocker "
         "if it is not a migration you need."),
    ],
    ("data", "dependency_down"): [
        ("Restore {svc}: restart or fail over to the replica",
         "{svc} stopped answering (or stopped reporting) before its callers failed",
         "Check the {svc} pod or instance status and logs; restart it or promote the replica, then verify "
         "callers reconnect. For redis: redis-cli -h <host> PING, then INFO memory for an OOM or eviction storm."),
        ("Let callers degrade gracefully while {svc} is down",
         "callers turn every {svc} failure into user-facing errors",
         "Enable the fallback path in {callers} (for sessions: fall back to token validation without the cache), "
         "and lower the client connect timeout to 500 ms."),
    ],
    ("data", "error_spike"): [
        ("Inspect {svc} server errors and resource limits (disk, memory, connections)",
         "{svc} error signals started before its callers failed",
         "Check {svc} logs for the first error after onset, then disk usage, memory and connection counts."),
    ],
    ("app", "error_spike_after_deploy"): [
        ("Roll back {svc} to {prev}",
         "{svc} errors started shortly after the {ver} change and no dependency of it failed first",
         "kubectl rollout undo deploy/{svc} (back to {prev}); watch {svc} error_rate return to baseline within "
         "2 minutes, then open a ticket against {ver}."),
    ],
    ("app", "error_spike"): [
        ("Drain or restart the failing {svc} replicas and check its recent config changes",
         "{svc} errors started before its callers degraded and none of its dependencies were anomalous first",
         "kubectl get pods -l app={svc}; restart the pods with the highest error count one at a time and check "
         "for config or feature-flag changes in the last hour."),
    ],
    ("app", "latency_spike"): [
        ("Scale out {svc} and look for a hot code path or GC pressure",
         "{svc} latency rose first while its dependencies stayed healthy",
         "kubectl scale deploy/{svc} --replicas=<current+2>; capture a profile from one slow replica."),
    ],
    ("app", "dependency_down"): [
        ("Restart {svc} and check its health endpoint",
         "{svc} stopped answering or reporting before its callers failed",
         "kubectl rollout restart deploy/{svc}; curl the /health endpoint of each replica."),
    ],
    ("app", "connection_pool_exhausted"): [
        ("Raise {svc} connection pool headroom and kill leaked connections",
         "{svc} reports an exhausted connection pool",
         "Restart the replica with the most open connections and set a 2 s pool acquire timeout."),
    ],
    ("external", "latency_spike"): [
        ("Open the circuit breaker to {svc} and fail over to the secondary PSP",
         "{svc} (external bank) latency rose first, so retries and timeouts pile up in its callers",
         "Set PSP_ROUTING=secondary (or flip the psp_failover flag), set the {svc} client timeout to 2 s with "
         "at most 1 retry, and tell the bank's support desk."),
    ],
    ("external", "error_spike"): [
        ("Fail over payments to the secondary PSP",
         "{svc} (external) is returning errors before its callers failed",
         "Flip the psp_failover flag, then queue failed payments for retry once {svc} recovers."),
    ],
    ("external", "dependency_down"): [
        ("Fail over to the secondary PSP and queue payments for retry",
         "{svc} (external) is unreachable",
         "Flip the psp_failover flag and confirm the payment success rate recovers."),
    ],
    ("edge", "latency_spike"): [
        ("Shed load at {svc}: enable rate limiting and check upstream capacity",
         "{svc} latency rose first and no dependency was anomalous before it",
         "Enable the gateway rate limit (for example 200 req/s per client) and check LB health checks."),
    ],
    ("edge", "error_spike"): [
        ("Check {svc} routing and TLS config, roll back the last gateway config change",
         "{svc} errors started first and no dependency was anomalous before it",
         "Diff the current gateway config against the last known good one and roll back if it changed."),
    ],
}
GENERIC = [("Investigate {svc} first: it is the most likely origin",
            "{svc} has the highest score: it shows anomalies before or beneath the other affected services",
            "Open the {svc} dashboard and logs at the incident onset and check its recent changes and dependencies.")]


def _fill(t: str, **kw) -> str:
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t


def _change_vals(c: Scored) -> tuple[str, str]:
    if c.change_event is None:
        return "the new version", "the previous version"
    a = c.change_event.attrs or {}
    return str(a.get("version") or "the new version"), str(a.get("previous_version") or "the previous version")


def fix_id_for(c: Scored) -> Optional[str]:
    """Fix the simulator can apply for this root (contracts FIX_IDS), first rule wins."""
    if c.service == "postgres" and c.signature == "connection_pool_exhausted":
        return "raise_pool_postgres"
    if c.signature == "error_spike_after_deploy":
        return "rollback_deploy"
    if c.service == "psp-adapter":
        return "circuit_breaker_psp"
    # Any other root (down, latency or error spike on a non-PSP service): restart it.
    # The simulator only heals when the fix matches the real fault (applied=false
    # otherwise), so a wrong diagnosis can never fake a recovery.
    return "restart_service"


def _for_root(c: Scored, group: dict[str, Episode], topo: TopoIndex) -> list[Recommendation]:
    tier = topo.tier.get(c.service, "app")
    sig = c.signature
    rules = RULES.get((tier, sig))
    if rules is None and sig == "error_spike_after_deploy":
        rules = RULES[("app", "error_spike_after_deploy")]
    rules = rules or GENERIC
    ver, prev = _change_vals(c)
    callers = sorted(s for s in topo.callers.get(c.service, ()) if s in group) or \
        sorted(topo.callers.get(c.service, ())) or ["its callers"]
    ev = c.evidence[:4]
    out = []
    for i, (a, w, r) in enumerate(rules):
        kw = dict(svc=c.service, ver=ver, prev=prev, callers=" and ".join(callers))
        evidence = ev
        if i > 0:  # follow-up rules cite the callers' onsets when there are any
            caller_ids = [group[s].onset_point.event_id for s in callers if s in group]
            evidence = caller_ids or ev
        out.append(Recommendation(action=_fill(a, **kw), why=_fill(w, **kw), evidence=list(evidence),
                                  runbook=_fill(r, **kw), fix_id=fix_id_for(c) if i == 0 else None))
    return out


def build(verdict: Verdict, group: dict[str, Episode], topo: TopoIndex) -> list[Recommendation]:
    cands = verdict.candidates
    top1 = cands[0]
    top2: Optional[Scored] = cands[1] if len(cands) > 1 else None
    recs: list[Recommendation] = []
    if verdict.abstain:
        pair = [c for c in (top1, top2) if c is not None]
        # cheap, reversible checks first
        for c in pair:
            if c.change_event is not None:
                ver, prev = _change_vals(c)
                recs.append(Recommendation(
                    action=f"Roll back one {c.service} canary to {prev} and compare its error rate",
                    why=f"cheap and reversible: if the rolled-back replica recovers, the {ver} change is the cause",
                    evidence=[c.change_event.id, group[c.service].onset_point.event_id],
                    runbook=f"kubectl rollout undo deploy/{c.service} on one replica (back to {prev}); compare "
                            f"error_rate of old vs new replicas for 2 minutes before rolling back the rest."))
        for c in pair:
            tier = topo.tier.get(c.service)
            if tier == "data":
                recs.append(Recommendation(
                    action=f"Identify who holds locks and connections on {c.service} before blaming either side",
                    why=f"{c.service} is a candidate, but the data does not yet show whether it degraded first",
                    evidence=[group[c.service].onset_point.event_id],
                    runbook="psql -c \"SELECT pid, application_name, wait_event_type, state, query FROM "
                            "pg_stat_activity WHERE wait_event_type IS NOT NULL;\""))
            elif tier == "external":
                recs.append(Recommendation(
                    action=f"Check the {c.service} provider status and per-bank latency",
                    why=f"{c.service} is a candidate; an external outage would explain the other symptoms",
                    evidence=[group[c.service].onset_point.event_id],
                    runbook="Open the PSP status page and split payment latency by provider."))
        names = " or ".join(c.service for c in pair)
        recs.append(Recommendation(
            action=f"Hold irreversible fixes until the root cause is decided ({names})",
            why=verdict.abstain_reason or "the engine abstained",
            evidence=_dedupe([group[c.service].onset_point.event_id for c in pair]),
            runbook="Collect the data listed under 'what would disambiguate', then re-run the analysis."))
        return recs

    recs.extend(_for_root(top1, group, topo))
    closure = topo.callers_closure(top1.service)
    victims = [c for c in cands[1:] if c.service in closure]
    if victims:
        names = [c.service for c in victims]
        recs.append(Recommendation(
            action=f"Do not roll back or restart {', '.join(names)}: they are victims of {top1.service}",
            why=f"they depend on {top1.service} directly or indirectly, and their failures follow from it",
            evidence=_dedupe(group[s].onset_point.event_id for s in names),
            runbook=f"Hold changes on {', '.join(names)}. Re-check their error_rate after {top1.service} recovers."))
    herring = [c for c in cands[1:] if c.change_event is not None]
    for c in herring:
        ver, prev = _change_vals(c)
        recs.append(Recommendation(
            action=f"Keep the {c.service} {ver} change in place for now: it is not the cause",
            why=f"the {c.service} change preceded its errors, but {top1.service} scores {top1.score:.3f} vs "
                f"{c.score:.3f} because {c.service} is downstream of {top1.service} failures",
            evidence=_dedupe([c.change_event.id, group[c.service].onset_point.event_id]),
            runbook=f"Do not roll back {c.service}. If its errors persist after {top1.service} recovers, "
                    f"revisit {ver}."))
    return recs


def _dedupe(xs) -> list[str]:
    return list(dict.fromkeys(xs))
