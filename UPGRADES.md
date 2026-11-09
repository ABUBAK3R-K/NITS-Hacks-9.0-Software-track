# UPGRADES (judge-review round, 9 Nov 2026)

Read CLAUDE.md first; every rule there still applies. This file adds scope on top of TASKS.md.
Goal: kill the objection "of course it works, you wrote both halves" and make "Justified Analysis, Reasoning & Visualization" literal on screen.

## Priority (do in this order, cut from the bottom)
Tier 1, must ship tonight:
- U1 Fault primitives + sandbox runs + Blind mode ("Judge's Chaos" + Reveal) + "Design your own outage" panel
- U2 "Why not X?" ruled-out reasons (deterministic, no LLM)
- U3 Eval harness: 100 blind runs vs 3 baselines, one results table
- U4 "Naive AI mode" side-by-side on the incident page
Tier 2, only if Tier 1 is merged by 17:30:
- U5 Apply fix -> sim heals -> lifecycle reaches RESOLVED, MTTD / MTTR / noise reduction computed
- U6 Alertmanager + Grafana webhook ingest (pure parsing, can run in parallel any time)
- U7 Cheap propagation animation when an incident forms (UI only)
Cut: Ask-J.A.R.V.I.S. chat, PDF export, Slack preview, OpenTelemetry, SLO burn (we define no SLOs, it would be decoration), full replay scrubber.
Hard rule: anything not merged by 18:30 is dropped. 19:00 freeze and 19:30 stop are unchanged.

## Honesty guardrails (non-negotiable; a judge will ask)
- Weights are frozen before the final eval. If you tune, tune on seeds 0-49 only and report seeds 50-99 only. Log it in DECISIONS.md.
- The naive LLM baseline gets a neutral prompt written before seeing any results, temperature 0, same model as the narrator, same events. Never weaken it to make us look good.
- Report whatever the numbers are. If the engine scores badly, fix the engine (with the split above). If the naive LLM scores well, the pitch becomes "same accuracy, plus every claim is auditable and it abstains" and we say so.
- The UI's naive-mode panel shows the naive LLM's measured accuracy from the eval next to its single answer, so one lucky or unlucky run never stands alone.
- The eval is on our own simulator with randomized blind faults. Say exactly that in the PPT. RCAEval real-data numbers remain a final-round item.
- Every number shown on screen (MTTD, MTTR, accuracy, confidence) is computed, never typed.

## Contract additions (main session, 15 min, before fan-out)
```
FaultSpec   {target: service_id, kind: "pool_exhaustion|latency|error_spike|down|bad_deploy",
             onset_jitter_s: float, decoy: {service: service_id, offset_s: float} | null, seed: int}
Run         {run_id, mode: "blind|designed|live", created_at, spec_hidden: bool, spec: FaultSpec | null}
Candidate   += {ruled_out_reason: str | null, ruled_out_evidence: [event_id]}
Incident    += {lifecycle: "DETECTED|SPREADING|CAUSE_IDENTIFIED|MITIGATING|RESOLVED",
                lifecycle_history: [{state, ts}], mttd_s: float|null, mttr_s: float|null,
                noise: {alerts: int, incidents: int, ratio: float}}
Recommendation += {fix_id: str | null}   # present when the sim can apply it
NaiveAnswer {provider, model, root_cause_service: str|null, raw_text, latency_ms,
             measured: {top1: float, n: int} | null}
EvalResults {n, seeds, split, methods: [{name, top1, top3, abstain_rate, false_blame_rate}],
             sensitivity: [{weight, minus50_top1, plus50_top1}]}
```
New endpoints:
- `POST /api/runs/blind` -> Run (spec hidden). `POST /api/runs/designed` body FaultSpec -> Run. `GET /api/runs/{id}/reveal` -> FaultSpec + whether our #1 matched.
- `POST /api/incidents/{id}/apply_fix {fix_id}` -> sim remediation; lifecycle moves only when the engine sees metrics back to baseline.
- `GET  /api/incidents/{id}/naive` -> NaiveAnswer.
- `GET  /api/eval/results` -> EvalResults (served from `eval/results.json`).
- `POST /api/events` additionally accepts Alertmanager and Grafana webhook JSON as-is (detect by shape).

## U1 details
- Generalize faults into `FaultSpec` primitives; the 5 named Chaos Lab buttons become presets of FaultSpec.
- Blind/designed runs execute in an isolated sandbox keyed by run_id (own event store namespace, fast-forwarded simulation, no real-time sleep), so several judges on the deployed link never collide with each other or with the live Chaos Lab.
- Blind spec: random target, random kind valid for that target (bad_deploy only on app services, pool_exhaustion only on postgres, etc.), onset jitter 0 to 20 s, decoy deploy on a different service 50% of the time at offset -60 to +30 s.
- UI: "Judge's Chaos" button (label hidden) and a "Design your own outage" form (service, fault, optional decoy service + offset). After the engine answers, a "Reveal" button shows the injected spec and a match / no-match badge.

## U2 details
- For every candidate except the winner: pick the single factor that cost it the most vs the winner, render one plain sentence with real numbers and evidence ids, e.g. "ledger-svc (deploy v1.8): ruled out. Went bad 42 s after postgres, and its own dependency postgres is broken, so the victim penalty applied." Numbers come from the engine, never hardcoded.
- Unit tests: red_herring produces a ruled-out reason for the decoy that mentions onset order or victim penalty.

## U3 details (`eval/run_eval.py`, headless, no server needed)
- 100 blind specs from fixed seeds (20 per fault kind), run through sim -> engine.
- Methods on the same runs: J.A.R.V.I.S.; "latest deploy" (blame the service with the most recent change event, else most alerts); "most alerts" (blame the service with the most alert/error events); "LLM only" (naive prompt over a compact event dump). If Groq rate limits hit, run the LLM baseline on a stated fixed subset (e.g. n=40) and report n.
- Metrics: top-1, top-3, abstain rate, false-blame rate (confident and wrong). Abstained counts as not top-1 for top-1 but is not a false blame.
- Sensitivity: each weight at -50% and +50%, report top-1 range.
- Outputs: `eval/results.json` + `eval/RESULTS.md` (one table, ready to paste into a slide) + the command to reproduce.

## U4 details
- Naive prompt (frozen): "Here are the events from a production system during an incident. What is the single root cause service? Answer with the service id and a short explanation." + compact events. Shared function with the eval baseline.
- Incident page toggle "Naive AI mode": two columns, naive answer vs J.A.R.V.I.S. answer with its evidence chips and why-not reasons; naive column footer shows its measured top-1 and n from EvalResults.

## U5 details
- Rule-table fixes that the sim can apply: raise pool size / kill long queries (postgres), roll back deploy (any bad_deploy), open circuit breaker to PSP (psp-adapter), restart (down).
- Lifecycle: DETECTED (incident formed) -> SPREADING (anomalous set grew in last 10 s) -> CAUSE_IDENTIFIED (decided, not abstained) -> MITIGATING (fix applied) -> RESOLVED (all incident services back within baseline for 15 s).
- MTTD = fault injection to DETECTED; MTTR = fault injection to RESOLVED; noise ratio = alerts / incidents. Show as small metric chips; the graph animates red to green.

## U6 details
- Map Alertmanager `alerts[].labels.service|job`, `startsAt`, `status`, `annotations.summary` and Grafana `alerts[]` equivalents to Event. Fixtures from their documented payload shapes. Add two curl examples to README.

## UI copy changes (all lanes)
- Title tagline: "Many alerts. One cause. Proof attached."
- Report guardrail chip: shield icon, "Guardrail blocked N uncited sentences" (never phrase it as an error).
- Header one-liner: "Our AI is not allowed to decide anything. It can only explain what the math proved."

## Lanes (worktrees)
- Lane E (sim + runs + fixes, starts after Lane A is merged): U1 backend, U5 backend.
- Lane F (engine + eval, starts after Lane B is merged): U2, lifecycle + metrics, then U3 once E's FaultSpec runner exists (stub it against FaultSpec until then).
- Lane G (UI, can start now from Lane C): judge panel, design form, reveal badge, why-not panel, naive side-by-side, shield chip, lifecycle badge + metric chips, Apply fix button, U7 animation. Build against new fixtures first.
- Lane H (narrator + ingest + docs, can start now from Lane D): shared naive-LLM function + /naive endpoint, U6, README curl examples, update docs/ppt_content.md with the new demo beat and an eval slide placeholder that pulls from eval/RESULTS.md.

## Revised video beat (< 2 min)
0:00 Real outage opener (NPCI UPI, 12 Apr 2025), many apps failing, one cause. 0:12 Judge's Chaos, label hidden, alert flood, propagation animates. 0:35 collapse to one incident (noise ratio chip). 0:45 root cause card, evidence chip opens a raw event. 0:58 "Why not ledger-svc?" one line. 1:05 Naive AI mode: show its answer next to ours. 1:12 Reveal: injected fault matches. 1:18 Apply fix, graph heals, RESOLVED, MTTR chip. 1:35 report with shield chip. 1:45 accuracy table, 100 blind runs vs baselines. 1:52 tagline.
Record only what actually works at 19:30. If naive mode happens to answer correctly on the recorded run, keep it and let its measured accuracy chip carry the point.
