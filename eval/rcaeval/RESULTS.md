# J.A.R.V.I.S. on RCAEval RE1-OB (real labelled failures)

RE1-OB from RCAEval (Pham et al., WWW 2025), 125 labelled failures from the Online Boutique microservice system. Metrics only (CPU, memory, load, latency, error per service, 1 s apart); faults: cpu, mem, disk, delay, loss on 5 services, 5 repetitions each.

## Lane R: engine changes measured on held-out cases

Honesty protocol, fixed and logged in DECISIONS.md before any tuning: the 125 cases are split by RCAEval's repetition number (the trailing integer in `re1ob_<service>_<fault>_<n>`). DEV = repetitions 1, 2, 3 (75 cases), TEST = repetitions 4, 5 (50 cases). Every engine change was chosen by looking at DEV only. The changes were then frozen and committed, and TEST was run exactly once, with the old engine and with the new one. No case is filtered, nothing inside the engine knows about RCAEval, and the engine never sees labels or inject_time. "Before" is the engine Lane K measured (plus only the speed fix, which leaves every answer unchanged).

### TEST (held out, 50 cases): the headline

| Run | Protocol | n | AC@1 | AC@3 | Avg@5 | Confident top-1 | Abstain | False blame | No incident |
|---|---|---|---|---|---|---|---|---|---|
| before | blind | 50 | 24.0% | 52.0% | 44.4% | 12.0% | 54.0% | 34.0% | 0 |
| after | blind | 50 | 48.0% | 60.0% | 59.2% | 36.0% | 44.0% | 20.0% | 1 |
| before | windowed | 50 | 24.0% | 28.0% | 26.8% | 18.0% | 38.0% | 44.0% | 14 |
| after | windowed | 50 | 40.0% | 42.0% | 41.6% | 34.0% | 30.0% | 36.0% | 11 |

### DEV (75 cases, used for tuning, so optimistic)

| Run | Protocol | n | AC@1 | AC@3 | Avg@5 | Confident top-1 | Abstain | False blame | No incident |
|---|---|---|---|---|---|---|---|---|---|
| before | blind | 75 | 33.3% | 56.0% | 53.9% | 14.7% | 66.7% | 18.7% | 1 |
| after | blind | 75 | 58.7% | 68.0% | 69.3% | 34.7% | 50.7% | 14.7% | 1 |
| before | windowed | 75 | 16.0% | 21.3% | 20.5% | 12.0% | 49.3% | 38.7% | 26 |
| after | windowed | 75 | 29.3% | 30.7% | 30.4% | 24.0% | 42.7% | 33.3% | 25 |

### TEST by fault type

| Fault | n | blind AC@1 before | blind AC@1 after | blind false blame before | after | windowed AC@1 before | after |
|---|---|---|---|---|---|---|---|
| cpu | 10 | 0.0% | 0.0% | 20.0% | 20.0% | 0.0% | 20.0% |
| delay | 10 | 60.0% | 60.0% | 30.0% | 20.0% | 50.0% | 50.0% |
| disk | 10 | 30.0% | 80.0% | 30.0% | 20.0% | 30.0% | 60.0% |
| loss | 10 | 10.0% | 50.0% | 60.0% | 30.0% | 20.0% | 30.0% |
| mem | 10 | 20.0% | 50.0% | 30.0% | 10.0% | 20.0% | 40.0% |

### What changed in the engine

1. **Sustained-anomaly rule for high-frequency metrics** (`anomaly.py`, `hf_interval_s`, `hf_confirm_s`). A metric sampled every second or faster only pages when its anomalous run lasts at least 5 s, or when one sample is critical (z >= 20). RE1's 1 s rows repeat one slower scrape for several seconds, so before this one noisy scrape already looked like two anomalous samples in a row. Slower signals keep the old two-sample rule, so the simulator's output is byte-identical.
2. **z_strong 10 -> 20** (`config.py`): a lone sample pages on its own only at the level the engine already calls critical.
3. **Incident selection** (`eval/run_eval.py primary_incident`, used by both evals): the incident still active at the end of the data comes first, then the old order (most services, most alerts, earliest). An on-call responder opens what is still firing, not the biggest incident that already closed.

Weights, the abstain rule, grouping and the four score components are unchanged. Tried on DEV and dropped (FAILURES.md): a median + IQR baseline, a higher z threshold, a larger std floor, a longer warm-up, re-learning or leaking the baseline after a level shift, a two-sided update gate, and treating repeated values as one scrape (best on DEV, but it moved incident ids when a saturated metric recovered).

## All 125 cases (reference only: includes the dev cases)

Weights {'upstream': 0.4, 'precedence': 0.3, 'change': 0.2, 'severity': 0.1}. No case filtering: n = 125 of 125 cases, 0 excluded.

| Run | Protocol | n | AC@1 | AC@3 | Avg@5 | Confident top-1 | Abstain | False blame | No incident |
|---|---|---|---|---|---|---|---|---|---|
| before | blind | 125 | 29.6% | 54.4% | 50.1% | 13.6% | 61.6% | 24.8% | 1 |
| after | blind | 125 | 54.4% | 64.8% | 65.3% | 35.2% | 48.0% | 16.8% | 2 |
| before | windowed | 125 | 19.2% | 24.0% | 23.0% | 14.4% | 44.8% | 40.8% | 40 |
| after | windowed | 125 | 33.6% | 35.2% | 34.9% | 28.0% | 37.6% | 34.4% | 36 |

- **blind**: the incident a responder opens first (still active at the end of the data, then most services, most alerts, earliest) among everything the engine opened in the case. Uses only the metrics.
- **windowed**: the same rule, but only over incidents opened at or after the case's `inject_time`. The RCAEval baselines are all given `inject_time`, so this row is the like-for-like one. The engine never sees the label or the inject time.
- AC@k follows RCAEval: the true root-cause service is in the top k of the ranking. We count the ranking even when the engine abstains. Confident top-1 = committed and right. Abstain = no committed root (ABSTAINED or no incident). False blame = committed and wrong. A case with no incident is a miss.
- Avg@5 is the mean of AC@1 to AC@5, the metric of Pham et al. (ASE 2024, arXiv 2408.13729), computed from the same ranking (a miss counts as 0). Those published methods are given the failure time, so the windowed row is the like-for-like one.
- Chance level for AC@1 is about 8.3% (1 of 12 services in the call graph).

## By fault type (blind)

| Fault | n | AC@1 | AC@3 | Confident top-1 | Abstain | False blame |
|---|---|---|---|---|---|---|
| cpu | 25 | 24.0% | 40.0% | 16.0% | 76.0% | 8.0% |
| delay | 25 | 80.0% | 92.0% | 40.0% | 52.0% | 8.0% |
| disk | 25 | 80.0% | 88.0% | 48.0% | 32.0% | 20.0% |
| loss | 25 | 40.0% | 48.0% | 36.0% | 24.0% | 40.0% |
| mem | 25 | 48.0% | 56.0% | 36.0% | 56.0% | 8.0% |

## By fault type (windowed)

| Fault | n | AC@1 | AC@3 | Confident top-1 | Abstain | False blame |
|---|---|---|---|---|---|---|
| cpu | 25 | 20.0% | 20.0% | 20.0% | 12.0% | 68.0% |
| delay | 25 | 36.0% | 40.0% | 20.0% | 76.0% | 4.0% |
| disk | 25 | 52.0% | 52.0% | 40.0% | 56.0% | 4.0% |
| loss | 25 | 20.0% | 24.0% | 20.0% | 40.0% | 40.0% |
| mem | 25 | 40.0% | 40.0% | 40.0% | 4.0% | 56.0% |

## By root-cause service (windowed, AC@1)

| Service | n | AC@1 | AC@3 |
|---|---|---|---|
| adservice | 25 | 44.0% | 44.0% |
| cartservice | 25 | 24.0% | 24.0% |
| checkoutservice | 25 | 28.0% | 36.0% |
| currencyservice | 25 | 32.0% | 32.0% |
| productcatalogservice | 25 | 40.0% | 40.0% |

## Why: what the engine's anomaly step sees (eval/rcaeval/diagnose.py)

Engine step 1 only (`app.engine.anomaly.detect`, current defaults), run on every case. It explains the scores above and changes none of them. Before the Lane R changes the first column was 4.78 for all cases (9.64 cpu, 1.68 delay, 1.96 disk, 1.92 loss, 8.72 mem), see `diagnostics_before.json`.

| Fault | n | Services anomalous before the fault (of 12) | Root anomalous after inject | Root is the first anomalous service after inject | Root's anomaly continues an episode that began before inject |
|---|---|---|---|---|---|
| all | 125 | 3.91 | 88.8% | 50.4% | 20.0% |
| cpu | 25 | 8.64 | 96.0% | 72.0% | 56.0% |
| delay | 25 | 1.08 | 100.0% | 16.0% | 0.0% |
| disk | 25 | 1.08 | 88.0% | 60.0% | 0.0% |
| loss | 25 | 1.0 | 60.0% | 48.0% | 4.0% |
| mem | 25 | 7.76 | 100.0% | 56.0% | 40.0% |

## What this means

On the 50 held-out TEST cases, blind AC@1 went from 24.0% to 48.0%, AC@3 from 52.0% to 60.0% and Avg@5 from 44.4% to 59.2%, while false blame fell from 34.0% to 20.0%. The windowed row moved from 24.0% to 40.0% AC@1. TEST is small (10 cases per fault), so one case is 2 points; the gain is consistent with DEV, but treat the exact numbers as rough. The engine is still far weaker on real data than on our simulator (100% there), and it abstains in 44.0% of TEST cases.

What is still hard. (1) Long normal periods: in the 70 minute cpu and mem cases, latency series that were near zero in the first minutes lock the baseline low, so several services stay anomalous for the whole run and form one large incident before the fault. (2) Onset order: precedence and upstream assume the root turns anomalous first. With the current anomaly step the root is the first anomalous service after injection in only 50.4% of cases. (3) A cpu, mem or disk stress on one service need not slow its callers, so there may be no propagation pattern to score. (4) Windowed: when a fault starts within 5 minutes of a noise episode on an adjacent service, grouping joins it to the older incident, so no incident opens after inject_time (36 of 125 cases here). Our simulator spreads every fault cleanly from cause to symptoms, which is why it scores 100% there.

Next ideas, not done: ranking by how far each service moved from its own normal period (the idea behind BARO) would need a fifth score component, which the contract does not allow today.

Speed: `timeline.main_point` used to test `p in pref` on a list, which was quadratic (70 s of analyze on a 189k-event case). It is now one boolean with the same answer; that case takes about 1 s, and all 125 cases take 75 s on 7 workers.

## Published baselines

The RCAEval paper (arXiv 2412.17015, WWW 2025 companion) publishes one results table, Table 6, for RE2 on Train Ticket only. It has no RE1-OB AC@1 table: it points to the earlier study [6] (Pham et al., "Root Cause Analysis for Microservice System based on Causal Inference: How Far Are We?", ASE 2024, arXiv 2408.13729) for RE1. That study reports Avg@5 with the methods given the failure time, so the Avg@5 column above (windowed row) is the one to compare. No published number is quoted here.

## Data notes

- Label check: cases.parquet root_cause_service equals the service in the directory name for 125 of 125 cases.
- Columns `InboundPassthroughClusterIpv4_*` are not fed to the engine, present in `re1ob_productcatalogservice_cpu_3`. Reason: Istio's catch-all cluster for inbound traffic, not a service.
- Columns `PassthroughCluster_*` are not fed to the engine, present in 50 cases. Reason: Istio's catch-all cluster for traffic to unknown destinations, not a service.
- Columns `carts_*` are not fed to the engine, present in `re1ob_productcatalogservice_cpu_3`. Reason: not in the Online Boutique call graph.
- Columns `catalogue_*` are not fed to the engine, present in `re1ob_productcatalogservice_cpu_3`. Reason: not in the Online Boutique call graph.
- Columns `front-end_*` are not fed to the engine, present in `re1ob_productcatalogservice_cpu_3`. Reason: not in the Online Boutique call graph.
- Columns `frontend-check_*` are not fed to the engine, present in `re1ob_cartservice_disk_3`. Reason: the loadgenerator init container (waits for the frontend), kubernetes-manifests/loadgenerator.yaml.
- Columns `main_*` are not fed to the engine, present in all 125 cases. Reason: the loadgenerator container (Locust traffic client, kubernetes-manifests/loadgenerator.yaml).
- Columns `orders_*` are not fed to the engine, present in `re1ob_productcatalogservice_cpu_3`. Reason: not in the Online Boutique call graph.
- Every other column is used, with its original name as the event name.
- Null cells (metric not observed at that second, skipped, no fill): 4612.
- `re1ob_currencyservice_loss_1`: inject_time 16933142 is before the first metric row (it looks truncated in the source). Kept as is; in the windowed protocol every incident of this case therefore qualifies.
- `re1ob_productcatalogservice_cpu_3`: only 63 metric rows, all before inject_time (no faulty period in the data), and it also carries Sock Shop column names (carts, catalogue, orders, front-end). Kept and scored like every other case.
- No case was excluded.

## How to reproduce

```
pip install -r eval/rcaeval/requirements.txt
python -I -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='phamquiluan/RCAEval', repo_type='dataset', allow_patterns=['re1ob_*/*', 'cases.parquet', 'README.md'], local_dir='D:/rcaeval-data')"
python -I eval/rcaeval/run_rcaeval.py --data D:/rcaeval-data

# Lane R protocol (DEV = repetitions 1-3, TEST = repetitions 4-5)
python -I eval/rcaeval/run_rcaeval.py --split dev --tag dev_after      # tuning set
python -I eval/rcaeval/run_rcaeval.py --split test --tag test_after    # run once, after freezing
# before = the same commands from a checkout of commit 6ca3443 (speed fix only), with
#   --tag dev_before / --tag test_before --out <this repo>/eval/rcaeval
python -I eval/rcaeval/diagnose.py --data D:/rcaeval-data
```

Data: Hugging Face dataset `phamquiluan/RCAEval` (MIT licence), 252 files, 31.9 MB on disk, stored outside the repo. Per-case answers are in `eval/rcaeval/results.json`. Generated 2026-11-10T08:19:58.000Z.
