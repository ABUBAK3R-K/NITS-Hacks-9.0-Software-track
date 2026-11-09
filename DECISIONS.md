# DECISIONS

One short entry per non-obvious choice. Newest at the bottom. Format: time IST, decision, why.

- 14:12 Started Lane 0 at 14:12, not 12:00. Every checkpoint in TASKS.md shifts by about +2h10m, except freeze (19:00) and stop (19:30), which do not move. Scope gets cut by the polish order, never the deploy.
- 14:13 Renamed `CLAUDE (7).md` and `TASKS (3).md` to `CLAUDE.md` and `TASKS.md` so every session and subagent finds them.
- 14:14 Remote is `ABUBAK3R-K/NITS-Hacks-9.0-Software-track` (public, was empty). Branch is `main`.
- 14:16 Contract additions beyond the CLAUDE.md summary, all in `contracts/schemas.py`:
  - `Incident.evidence_events: Event[]` resolves every event id the incident references, so the evidence drawer and the narrator need no extra endpoint. It doubles as the evidence set for citation validation.
  - `Incident.title`, `updated_at`, `abstain_reason`, `disambiguation[]`. ABSTAINED needs "what data would disambiguate" as data, not UI copy.
  - `Candidate.penalty`, `victim_of`, `onset`, `signature`, `component_evidence{upstream,precedence,change,severity}`. These satisfy the rule "record every component with the event ids that produced it".
  - `GET /api/incidents` returns `IncidentSummary[]` (no candidates or events), which keeps it cheap.
  - `TopologyState{ts, services[{service, health, is_root_cause}], hot_edges}` defines the `topology_state` SSE payload, which CLAUDE.md left unspecified.
  - `Report.incident_id`, `generated_at`. `SimStatus` and `IngestResult` are the response bodies for sim and ingest endpoints.
- 14:16 Score components are stored already weighted (upstream <= 0.40 etc.), so `score = sum(components) * penalty` and the UI can draw stacked bars directly.
- 14:16 Exact formulas used for fixtures (Lane B implements the same): upstream = anomalous services that transitively call it / (anomalous count - 1); precedence = share of its anomalous transitive callers whose onset is later than its own (0 if it has none); change = 1 if a change event on it within 10 min before onset; severity = its peak z / max peak z in the incident.
- 14:16 `alert_count` = number of `kind == "alert"` events grouped into the incident. The hero fixture has 14 alerts, so the headline reads "14 alerts, 1 cause".
- 14:16 Timestamps are real UTC with a trailing Z. 14:02 IST is stored as 08:32Z and the UI renders in IST. The CLAUDE.md example `14:02:11Z` was illustrative.
- 14:17 Fixtures come from `contracts/gen_fixtures.py` (seeded, reproducible), which applies the real formula to hand-set onsets, not hand-typed JSON. The hero fixture yields postgres at 0.80 (OPEN). The ambiguous fixture yields ledger-svc 0.433 vs postgres 0.430 (ABSTAINED: margin 0.003, top score < 0.45).
- 14:17 The ambiguous scenario is a ledger-svc v4.2.0 deploy (with a migration) followed 31 s later by ledger-svc lock-timeout errors. postgres `pg_lock_wait_ms` crosses threshold last because of 2 s sampling. The victim penalty halves ledger-svc, and postgres gets no precedence, so the scores tie. This is the honest "we cannot tell yet" case, and its disambiguation list asks for 1 s lock-wait data and per-version error rates.
- 14:17 In the hero fixture the alerts fire out of causal order on purpose (the gateway pages first, the postgres alert fires sixth). Onsets come from metrics and logs, not alert order. This is the pitch: first-to-page is not root cause.
- 14:18 The backend imports contracts through `backend/app/schemas.py`, which puts the repo root on `sys.path`. Render runs from the repo root (`render.yaml`) so `contracts/` is present.
- 14:18 The frontend copies `contracts/types.ts` and fixtures into `frontend/lib/contracts/` with `npm run sync-contracts` (runs on predev and prebuild). The copy is committed, so Vercel builds even if it cannot see files outside `frontend/`.
- 14:18 There is one shared Python venv at `<repo>/.venv` (gitignored). Worktrees use it by absolute path instead of each installing their own. Local Python is 3.12 and Render pins 3.11.
- 14:24 Git workflow (team rule): every change goes on a feature branch, commits stay small, there are no co-author trailers, and nothing is committed without explicit approval. Lane 0 lands on `lane-0-contracts`, then merges to `main` once approved. Lane worktrees are already feature branches.
- 14:24 `.gitattributes` sets `merge=union` on DECISIONS.md and FAILURES.md, so four lanes appending entries never conflict.
- 14:25 The fixture event streams keep degraded metrics after onset (every 2 s scrape), and failed requests start at each service's own onset, so anomalies are sustained and onset order is unambiguous for the engine. Hero impact is now 57 users, 38 failed payments, INR 45,462.
