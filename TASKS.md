# TASKS (times IST, 9 Nov 2026, assumes start ~12:00)

Parallel plan: Lane 0 runs alone on `main`. Then four worktrees run in parallel, each with its own Claude Code session (or subagent), merging back at the integration checkpoints.

```
git worktree add ../jarvis-sim    -b lane-a-sim
git worktree add ../jarvis-engine -b lane-b-engine
git worktree add ../jarvis-ui     -b lane-c-ui
git worktree add ../jarvis-narr   -b lane-d-narrate
```

## Lane 0: contracts + scaffold (main, 12:00 to 12:40, BLOCKS everything)
- [ ] Monorepo scaffold: `backend/` (FastAPI hello + CORS + /health), `frontend/` (Next.js + Tailwind + @xyflow/react + recharts), `contracts/`
- [ ] `contracts/schemas.py`: Event, Topology, Incident, Candidate, Impact, TimelineEntry, Recommendation, Report (pydantic v2)
- [ ] `contracts/types.ts`: same shapes in TypeScript
- [ ] `contracts/fixtures/`: topology.json, incidents_list.json, incident_db_pool.json (OPEN), incident_ambiguous.json (ABSTAINED), report_db_pool.json, sse_sample.txt
- [ ] Empty DECISIONS.md, FAILURES.md, README.md; push; create the 4 worktrees

## Lane A: simulator + ingest (12:40 to 15:30)
- [ ] `sim/topology.py` the 7-service payments graph from CLAUDE.md
- [ ] `sim/generator.py` steady traffic: request events (with user_id, amount_inr on payments), latency/error_rate metrics every 2 s per service
- [ ] `sim/faults.py` 5 faults with propagation along callers, realistic onset lag (300 to 1500 ms per hop)
- [ ] `/api/sim/start|fault|reset`, background task pushing into `store.py`
- [ ] `/api/stream` SSE (events throttled + incident_update + topology_state)
- [ ] `ingest/` `/api/events` and `/api/upload` (jsonl, csv, plain log line regex) + `samples/payment_outage.log` for the upload demo
- [ ] tests: each fault produces anomalies on the expected services only

## Lane B: engine (12:40 to 15:30) <- most important lane, give it the strongest session
- [ ] `engine/anomaly.py` EWMA z-score, onsets, alert/error-burst shortcut
- [ ] `engine/grouping.py` window + graph-connected grouping
- [ ] `engine/rca.py` scoring components with evidence ids, victim penalty, abstain rule
- [ ] `engine/impact.py`, `engine/timeline.py`, `engine/recommend.py` (rule table)
- [ ] `engine/run.py` `analyze(events, topology) -> list[Incident]`, recomputed on new events (debounced 1 s)
- [ ] tests on synthetic event lists: db_pool -> postgres, bad_deploy -> payments-svc, redis_down -> separate small incident, red_herring -> postgres or ABSTAINED (never ledger as confident root), psp_slow -> psp-adapter

## Lane C: dashboard (12:40 to 16:00, builds against fixtures, swaps to live API at integration)
- [ ] Top bar: "Chaos Lab" buttons (start, 5 faults, reset), upload button, live/paused indicator
- [ ] Center: React Flow service graph; node color = health; root cause node pulsing ring; animated edges along propagation path
- [ ] Left: incident list (severity, alert_count -> "14 alerts, 1 cause")
- [ ] Right: Root cause card: confidence bar, ranked candidates with score components, each evidence chip clickable -> raw event drawer. ABSTAINED state shows both candidates + "what would disambiguate"
- [ ] Bottom: incident timeline (onsets with caused_by arrows) + small Recharts latency/error sparkline
- [ ] Impact strip: services, users affected, failed payments, INR at risk
- [ ] `/incident/[id]`: generated report, citations as chips, "Export Markdown"
- [ ] Hero moment polish: alerts flood in red, then collapse into one incident card. This is the first 20 s of the video.

## Lane D: narrator + docs (12:40 to 15:00)
- [ ] `narrate/llm.py` provider interface: Groq JSON mode -> Gemini -> template; 8 s timeout
- [ ] Prompt: input = incident JSON with evidence events; output = `{summary, sentences:[{text,cites}]}`; instruct "only state what the evidence ids support"
- [ ] `narrate/grounding.py` drop sentences with unknown or missing cites, report `dropped` count (show it in UI: "2 unsupported sentences removed")
- [ ] Template narrative that reads well with zero LLM
- [ ] README: problem, architecture diagram (mermaid), how to run, how the engine decides, abstain rule
- [ ] Draft PPT content per slide (see below) into `docs/ppt_content.md`

## Integration checkpoints (main)
- [ ] 15:30 Merge A + B: sim -> store -> engine -> /api/incidents works; run all tests
- [ ] 16:00 Merge C: point UI at live API + SSE; fix contract drift
- [ ] 16:30 Merge D: report endpoint live
- [ ] 17:00 Deploy: Render (backend, env keys) + Vercel (frontend). Smoke test hero demo on the live URL
- [ ] 17:00 to 19:00 Polish + stretch, in this order: (1) hero demo flawless, (2) abstain demo, (3) upload demo, (4) redis_down "not over-grouping" demo, (5) anything else
- [ ] 19:00 FREEZE. 19:30 STOP.

## 19:30 to 20:45 Submission
- [ ] Demo video < 2 min (demo skill). Script: 0:00 problem (3 alerts, 3 teams paged) -> 0:15 hit db_pool_exhaustion, alerts flood -> 0:35 collapse to 1 incident, graph lights path to postgres -> 0:55 evidence chain + confidence -> 1:10 report with citations, dropped-sentence counter -> 1:25 red_herring: engine refuses to blame the deploy -> 1:45 close
- [ ] PPT in the official NITS template: problem, why existing alerting fails, solution, architecture, how the engine decides (scoring + abstain), demo screenshots, tech stack, impact, roadmap (RCAEval benchmark accuracy, OpenTelemetry ingest, Slack/PagerDuty hooks), team
- [ ] Submit on Unstop by 20:45 (15 min buffer)

## Final round (10 Nov, 17:00 to 20:00) backlog
- [ ] RCAEval RE1/RE2 Online Boutique subset: run engine, report AC@1 / AC@3 vs their published baselines
- [ ] OpenTelemetry/Prometheus-style ingest adapter
- [ ] Incident similarity: "seen this before" from past incidents
