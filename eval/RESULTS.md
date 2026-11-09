# Eval results (U3)

Evaluated on our own simulator with randomized blind faults; RCAEval real-data numbers are a final-round item.

100 blind runs, seeds 0-99.

| Method | n | Top-1 | Top-3 | Abstain | False blame |
|---|---|---|---|---|---|
| J.A.R.V.I.S. | 100 | 100% | 100% | 0% | 0% |
| Latest deploy | 100 | 25% | 68% | 0% | 75% |
| Most alerts | 100 | 47% | 99% | 0% | 53% |
| LLM only | 40 | 78% | 78% | 22% | 0% |

False blame = confident and wrong. Abstaining is never a false blame, and never a top-1 hit.

## Weight sensitivity (J.A.R.V.I.S. top-1)

| Weight | -50% | default | +50% |
|---|---|---|---|
| upstream | 90% | 100% | 100% |
| precedence | 100% | 100% | 100% |
| change | 100% | 100% | 100% |
| severity | 100% | 100% | 100% |

## Top-1 by fault kind

| Kind | J.A.R.V.I.S. | Latest deploy | Most alerts | LLM only |
|---|---|---|---|---|
| bad_deploy | 20/20 | 17/20 | 12/20 | 5/8 |
| down | 20/20 | 2/20 | 8/20 | 7/8 |
| error_spike | 20/20 | 2/20 | 11/20 | 7/8 |
| latency | 20/20 | 4/20 | 16/20 | 6/8 |
| pool_exhaustion | 20/20 | 0/20 | 0/20 | 6/8 |

Runs where the engine opened no incident at all: 0 (counted as misses).

## How it was run

Weights frozen before eval (engine defaults {'upstream': 0.4, 'precedence': 0.3, 'change': 0.2, 'severity': 0.1}). Blind specs (one per seed, kind balanced by seed) for seeds 0-99 (100 runs: 20 bad_deploy, 20 down, 20 error_spike, 20 latency, 20 pool_exhaustion; 39 with a decoy deploy), runner app.sim.runner, each run 60 s baseline + 90 s after injection. All methods scored on the same runs; expected root = spec.target. llm_only scored on the first 40 of these seeds (0-39), Groq openai/gpt-oss-120b only (no Gemini fallback), temperature 0, paced for the 8k tokens/min limit; 9 run(s) got no Groq answer after retries and count as no answer.

Reproduce from the repo root:

```
python eval/run_eval.py --seeds 0-99 --llm-n 40
```

## README

- `eval/run_eval.py` is headless: blind FaultSpec per seed, simulator, then the engine and the baselines on the same events. No server, no clock, deterministic for a fixed seed list.
- Baselines: `latest_deploy` blames the service with the most recent change event (fix_applied excluded), else the most alerts; `most_alerts` blames the service with the most alert or error-severity events; `llm_only` asks the frozen naive prompt (app.narrate.naive.ask_naive) for one service, so its top-3 equals its top-1 and a missing answer counts as an abstain.
- J.A.R.V.I.S. is scored on the incident a responder would open first (most services, then most alerts); ground truth is never used to pick it. ABSTAINED counts as not top-1 and not a false blame.
- Flags: `--no-llm`, `--llm-n N` (LLM on the first N seeds of the list, reported as its own n), `--seeds a-b`, `--no-sensitivity`.
- Honesty rule: weights are frozen before the final eval; any tuning uses seeds 0-49 only and the reported numbers then come from seeds 50-99 only (see DECISIONS.md).
