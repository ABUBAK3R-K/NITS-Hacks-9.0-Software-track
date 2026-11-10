"""Lane K: run the J.A.R.V.I.S. engine on RCAEval RE1-OB (125 labelled real failures).

Run from the repo root (data must already be in --data, see eval/rcaeval/RESULTS.md):

    python -I eval/rcaeval/run_rcaeval.py --data D:/rcaeval-data

For every case: metric columns -> Events (adapter.py), analyze(events, topology) with the
engine defaults, then score the incident a responder would open first
(eval/run_eval.py primary_incident: still active, then most services, most alerts, earliest).

Two ways to pick that incident, both reported:
  blind     over every incident the engine opened in the case. Uses nothing but the metrics.
  windowed  over incidents opened at or after the case's inject_time. Every RCAEval baseline
            is given inject_time as input, so this is the like-for-like setting.
The engine itself never sees the label or inject_time.

Metrics (per protocol):
  AC@1, AC@3      the true root cause is ranked 1st / in the top 3 of the incident's candidate
                  ranking (RCAEval's definition: a ranking, so it counts even when we abstain)
  confident_top1  we committed to a root (not ABSTAINED) and it is the true one
  abstain_rate    no committed root (ABSTAINED, or no incident at all)
  false_blame     committed to a root and it is wrong
A case where no incident is picked is a miss on every accuracy metric.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(HERE), str(ROOT / "backend"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import adapter as A  # noqa: E402
from app.engine.common import parse_ts  # noqa: E402
from app.engine.config import CONFIG  # noqa: E402
from app.engine.given_time import rank_at  # noqa: E402
from app.engine.run import analyze  # noqa: E402
from eval.run_eval import primary_incident  # noqa: E402

OUT_DIR = HERE
ENGINE_PROTOCOLS = ("blind", "windowed")  # the live engine (run.analyze)
PROTOCOLS = ENGINE_PROTOCOLS + ("given_time",)  # + the given-time ranking (engine/given_time.py)
SOURCE_SENTENCE = ("RE1-OB from RCAEval (Pham et al., WWW 2025), 125 labelled failures from the Online "
                   "Boutique microservice system")


def _answer(inc) -> dict:
    if inc is None:
        return {"incident": None, "ranking": [], "confident": False, "root": None}
    return {"incident": inc.id, "opened_at": inc.opened_at, "status": inc.status,
            "n_services": len(inc.services), "ranking": [c.service for c in inc.candidates][:5],
            "scores": [c.score for c in inc.candidates][:5],
            "confident": inc.root_cause is not None,
            "root": inc.root_cause.service if inc.root_cause else None}


def instance(case: str) -> int:
    """`re1ob_adservice_cpu_3` -> 3 (RCAEval's repetition number, the trailing integer)."""
    return int(case.rsplit("_", 1)[1])


# Honesty protocol (DECISIONS.md, Lane R): tune only on DEV, run TEST once at the end.
SPLITS = {"dev": (1, 2, 3), "test": (4, 5), "all": (1, 2, 3, 4, 5)}


def run_case(args) -> dict:
    data_dir, row, config = args
    t0 = time.perf_counter()
    case = A.load_case(Path(data_dir), row)
    label_from_dir = case.case.split("_")[1]
    t1 = time.perf_counter()
    incs = analyze(case.events, A.ob_topology(), config)
    after = [i for i in incs if parse_ts(i.opened_at) >= case.inject_time]
    gt_res = rank_at(case.events, float(case.inject_time), {**CONFIG, **(config or {})})
    given = {"incident": "given_time" if gt_res.ranking else None,
             "ranking": [c.service for c in gt_res.ranking][:5],
             "scores": [c.score for c in gt_res.ranking][:5],
             "metrics": [c.metric for c in gt_res.ranking][:5],
             "evidence": [c.evidence for c in gt_res.ranking][:5],
             "confident": gt_res.root is not None, "root": gt_res.root}
    return {"case": case.case, "fault": case.fault, "ground_truth": case.ground_truth,
            "label_matches_dir": label_from_dir == case.ground_truth,
            "inject_time": case.inject_time, "inject_iso": A.iso_utc(case.inject_time),
            "rows": case.n_rows, "events": len(case.events),
            "inject_in_range": case.n_rows > 0 and case.inject_time >= int(row["time_start"]),
            "dropped_columns": sorted(case.adapted.dropped_columns), "null_values": case.adapted.null_values,
            "n_incidents": len(incs), "n_incidents_after_inject": len(after),
            "blind": _answer(primary_incident(incs)), "windowed": _answer(primary_incident(after)),
            "given_time": given,
            "seconds": round(time.perf_counter() - t0, 1), "analyze_s": round(time.perf_counter() - t1, 1)}


def score(rows: list[dict], proto: str) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    a1 = a3 = conf1 = abst = fb = noinc = 0
    acs = [0] * 5  # AC@1..AC@5 hit counts
    for r in rows:
        a, gt = r[proto], r["ground_truth"]
        a1 += a["ranking"][:1] == [gt]
        a3 += gt in a["ranking"][:3]
        for k in range(5):
            acs[k] += gt in a["ranking"][:k + 1]
        conf1 += a["confident"] and a["root"] == gt
        abst += not a["confident"]
        fb += a["confident"] and a["root"] != gt
        noinc += a["incident"] is None
    r3 = lambda x: round(x / n, 3)  # noqa: E731
    # Avg@5 = mean of AC@1..AC@5 (the metric of Pham et al., ASE 2024); a miss counts as 0.
    return {"n": n, "ac1": r3(a1), "ac3": r3(a3), "ac5": r3(acs[4]), "avg5": round(sum(acs) / 5 / n, 3),
            "confident_top1": r3(conf1), "abstain_rate": r3(abst),
            "false_blame_rate": r3(fb), "no_incident": noinc}


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


ROW_HEAD = ("| Run | Protocol | n | AC@1 | AC@3 | Avg@5 | Confident top-1 | Abstain | False blame | No incident |",
            "|---|---|---|---|---|---|---|---|---|---|")


def row(label: str, proto: str, s: dict) -> str:
    return (f"| {label} | {proto} | {s['n']} | {pct(s['ac1'])} | {pct(s['ac3'])} | {pct(s['avg5'])} | "
            f"{pct(s['confident_top1'])} | {pct(s['abstain_rate'])} | {pct(s['false_blame_rate'])} | "
            f"{s['no_incident']} |")


def load_run(out_dir: Path, tag: str) -> list[dict] | None:
    f = Path(out_dir) / "runs" / f"{tag}.json"
    return json.loads(f.read_text(encoding="utf-8"))["cases"] if f.is_file() else None


def avg5_of(rows: list[dict], proto: str) -> float:
    return score(rows, proto)["avg5"]


def lane_s_section(out_dir: Path) -> list[str]:
    """Given-time ranking (engine/given_time.py): DEV and the single TEST run. Empty if missing."""
    dev, test = load_run(out_dir, "dev_given"), load_run(out_dir, "test_given")
    if dev is None or test is None:
        return []
    L = ["## Lane S: when the failure time is known (given_time.py)", "",
         "The live engine has to find the failure on its own. When the failure time is known (a responder "
         "says when it broke, or a benchmark supplies it, as every published RCAEval baseline gets), "
         "`app.engine.given_time.rank_at` ranks services by their largest robust metric shift: "
         "|median after - median before| / IQR before, as in BARO (Pham, Ha, Zhang, ASE 2024). Windows "
         "(600 s before, 300 s after) were chosen on DEV from three tried; the commit rule (top shift at least "
         "2x the second) was set before any run. Frozen and logged in DECISIONS.md, then TEST was run once.", "",
         *ROW_HEAD,
         row("TEST (held out)", "given_time", score(test, "given_time")),
         row("DEV (tuning)", "given_time", score(dev, "given_time")),
         row("TEST, live engine for comparison", "windowed", score(test, "windowed")), "",
         "| Fault | TEST n | TEST AC@1 | TEST Avg@5 | All 125 Avg@5 | BARO Avg@5 (published, 125) |",
         "|---|---|---|---|---|---|"]
    baro = {"cpu": 0.97, "mem": 1.00, "disk": 0.91, "delay": 0.98, "loss": 0.67}
    for f in sorted({r["fault"] for r in test}):
        t = [r for r in test if r["fault"] == f]
        a = t + [r for r in dev if r["fault"] == f]
        st = score(t, "given_time")
        L.append(f"| {f} | {st['n']} | {pct(st['ac1'])} | {pct(st['avg5'])} | {pct(avg5_of(a, 'given_time'))} | "
                 f"{baro[f]:.2f} |")
    L += ["", "This is the same family of method as BARO, so being in the same range is the expected result, "
          "not a new state of the art. What it adds here: every ranked service carries evidence event ids, "
          "and the dashboard shows it as a second opinion next to the engine's answer "
          "(`GET /api/incidents/{id}/shift_check`).", ""]
    return L


def lane_r_section(out_dir: Path) -> list[str]:
    """TEST before vs after (headline), DEV before vs after, by fault on TEST. Empty if the runs are missing."""
    runs = {t: load_run(out_dir, t) for t in ("test_before", "test_after", "dev_before", "dev_after")}
    if any(v is None for v in runs.values()):
        return []
    L = ["## Lane R: engine changes measured on held-out cases", "",
         "Honesty protocol, fixed and logged in DECISIONS.md before any tuning: the 125 cases are split by "
         "RCAEval's repetition number (the trailing integer in `re1ob_<service>_<fault>_<n>`). DEV = repetitions "
         "1, 2, 3 (75 cases), TEST = repetitions 4, 5 (50 cases). Every engine change was chosen by looking at "
         "DEV only. The changes were then frozen and committed, and TEST was run exactly once, with the old "
         "engine and with the new one. No case is filtered, nothing inside the engine knows about RCAEval, and "
         "the engine never sees labels or inject_time. \"Before\" is the engine Lane K measured (plus only the "
         "speed fix, which leaves every answer unchanged).", "",
         "### TEST (held out, 50 cases): the headline", "", *ROW_HEAD]
    for proto in ENGINE_PROTOCOLS:
        for t in ("test_before", "test_after"):
            L.append(row(t.split("_")[1], proto, score(runs[t], proto)))
    L += ["", "### DEV (75 cases, used for tuning, so optimistic)", "", *ROW_HEAD]
    for proto in ENGINE_PROTOCOLS:
        for t in ("dev_before", "dev_after"):
            L.append(row(t.split("_")[1], proto, score(runs[t], proto)))
    L += ["", "### TEST by fault type", "",
          "| Fault | n | blind AC@1 before | blind AC@1 after | blind false blame before | after | "
          "windowed AC@1 before | after |", "|---|---|---|---|---|---|---|---|"]
    for f in sorted({r["fault"] for r in runs["test_after"]}):
        b = [r for r in runs["test_before"] if r["fault"] == f]
        a = [r for r in runs["test_after"] if r["fault"] == f]
        sb, sa = score(b, "blind"), score(a, "blind")
        wb, wa = score(b, "windowed"), score(a, "windowed")
        L.append(f"| {f} | {len(a)} | {pct(sb['ac1'])} | {pct(sa['ac1'])} | {pct(sb['false_blame_rate'])} | "
                 f"{pct(sa['false_blame_rate'])} | {pct(wb['ac1'])} | {pct(wa['ac1'])} |")
    L += ["", "### What changed in the engine", "",
          "1. **Sustained-anomaly rule for high-frequency metrics** (`anomaly.py`, `hf_interval_s`, `hf_confirm_s`). "
          "A metric sampled every second or faster only pages when its anomalous run lasts at least 5 s, or when "
          "one sample is critical (z >= 20). RE1's 1 s rows repeat one slower scrape for several seconds, so "
          "before this one noisy scrape already looked like two anomalous samples in a row. Slower signals "
          "keep the old two-sample rule, so the simulator's output is byte-identical.",
          "2. **z_strong 10 -> 20** (`config.py`): a lone sample pages on its own only at the level the engine "
          "already calls critical.",
          "3. **Incident selection** (`eval/run_eval.py primary_incident`, used by both evals): the incident "
          "still active at the end of the data comes first, then the old order (most services, most alerts, "
          "earliest). An on-call responder opens what is still firing, not the biggest incident that already "
          "closed.",
          "",
          "Weights, the abstain rule, grouping and the four score components are unchanged. Tried on DEV and "
          "dropped (FAILURES.md): a median + IQR baseline, a higher z threshold, a larger std floor, a longer "
          "warm-up, re-learning or leaking the baseline after a level shift, a two-sided update gate, and "
          "treating repeated values as one scrape (best on DEV, but it moved incident ids when a saturated "
          "metric recovered).", ""]
    return L


def markdown(res: dict, out_dir: Path = OUT_DIR) -> str:
    o, L = res["overall"], []
    L += ["# J.A.R.V.I.S. on RCAEval RE1-OB (real labelled failures)", "",
          SOURCE_SENTENCE + ". Metrics only (CPU, memory, load, latency, error per service, 1 s apart); "
          "faults: cpu, mem, disk, delay, loss on 5 services, 5 repetitions each.", ""]
    L += lane_s_section(out_dir)
    L += lane_r_section(out_dir)
    before_all = None
    tb, db = load_run(out_dir, "test_before"), load_run(out_dir, "dev_before")
    if tb is not None and db is not None and res["split"] == "all":
        before_all = tb + db
    L += [f"## All {res['n_cases']} cases (reference only: includes the dev cases)", "",
          f"Weights {res['weights']}. No case filtering: n = {res['n_cases']} of {res['n_index']} cases, "
          f"{res['n_excluded']} excluded.", "", *ROW_HEAD]
    for p in PROTOCOLS:
        if before_all is not None and p in ENGINE_PROTOCOLS:
            L.append(row("before", p, score(before_all, p)))
        L.append(row("after" if before_all is not None else "current", p, o[p]))
    L += ["", "- **blind**: the incident a responder opens first (still active at the end of the data, then "
          "most services, most alerts, earliest) among everything the engine opened in the case. Uses only "
          "the metrics.",
          "- **windowed**: the same rule, but only over incidents opened at or after the case's "
          "`inject_time`. The RCAEval baselines are all given `inject_time`, so this row is the "
          "like-for-like one. The engine never sees the label or the inject time.",
          "- **given_time**: `app.engine.given_time.rank_at` at the case's `inject_time`: services ranked by "
          "their largest robust metric shift (median after vs before, over the IQR before). Committed when the "
          "top shift is at least 2x the second. This is the protocol of the published baselines.",
          "- AC@k follows RCAEval: the true root-cause service is in the top k of the ranking. We count "
          "the ranking even when the engine abstains. Confident top-1 = committed and right. "
          "Abstain = no committed root (ABSTAINED or no incident). False blame = committed and wrong. "
          "A case with no incident is a miss.",
          "- Avg@5 is the mean of AC@1 to AC@5, the metric of Pham et al. (ASE 2024, arXiv 2408.13729), "
          "computed from the same ranking (a miss counts as 0). Those published methods are given the failure "
          "time, so the windowed row is the like-for-like one.",
          f"- Chance level for AC@1 is about {pct(1 / res['n_topology_services'])} "
          f"(1 of {res['n_topology_services']} services in the call graph).", ""]
    for p in PROTOCOLS:
        L += [f"## By fault type ({p})", "", "| Fault | n | AC@1 | AC@3 | Confident top-1 | Abstain | False blame |",
              "|---|---|---|---|---|---|---|"]
        for f, d in res["by_fault"].items():
            s = d[p]
            L.append(f"| {f} | {s['n']} | {pct(s['ac1'])} | {pct(s['ac3'])} | {pct(s['confident_top1'])} | "
                     f"{pct(s['abstain_rate'])} | {pct(s['false_blame_rate'])} |")
        L.append("")
    L += ["## By root-cause service (windowed, AC@1)", "", "| Service | n | AC@1 | AC@3 |", "|---|---|---|---|"]
    for svc, s in res["by_service_windowed"].items():
        L.append(f"| {svc} | {s['n']} | {pct(s['ac1'])} | {pct(s['ac3'])} |")
    d = res.get("diagnostics")
    if d:
        L += ["", "## Why: what the engine's anomaly step sees (eval/rcaeval/diagnose.py)", "",
              "Engine step 1 only (`app.engine.anomaly.detect`, current defaults), run on every case. It explains "
              "the scores above and changes none of them. Before the Lane R changes the first column was 4.78 for "
              "all cases (9.64 cpu, 1.68 delay, 1.96 disk, 1.92 loss, 8.72 mem), see `diagnostics_before.json`.",
              "",
              "| Fault | n | Services anomalous before the fault (of 12) | Root anomalous after inject | Root is the first "
              "anomalous service after inject | Root's anomaly continues an episode that began before inject |",
              "|---|---|---|---|---|---|"]
        for f, s in [("all", d["overall"])] + list(d["by_fault"].items()):
            L.append(f"| {f} | {s['n']} | {s['mean_services_anomalous_before_inject']} | "
                     f"{pct(s['gt_anomalous_after_inject'])} | {pct(s['gt_first_after_inject'])} | "
                     f"{pct(s['gt_episode_started_before_inject'])} |")
    nb = sum(1 for r in res["cases"] if r["n_incidents_after_inject"] == 0)
    tests = {t: load_run(out_dir, t) for t in ("test_before", "test_after")}
    L += ["", "## What this means", ""]
    if all(v is not None for v in tests.values()):
        tb_, ta_ = score(tests["test_before"], "blind"), score(tests["test_after"], "blind")
        wb_, wa_ = score(tests["test_before"], "windowed"), score(tests["test_after"], "windowed")
        L += [f"On the 50 held-out TEST cases, blind AC@1 went from {pct(tb_['ac1'])} to {pct(ta_['ac1'])}, "
              f"AC@3 from {pct(tb_['ac3'])} to {pct(ta_['ac3'])} and Avg@5 from {pct(tb_['avg5'])} to "
              f"{pct(ta_['avg5'])}, while false blame fell from {pct(tb_['false_blame_rate'])} to "
              f"{pct(ta_['false_blame_rate'])}. The windowed row moved from {pct(wb_['ac1'])} to {pct(wa_['ac1'])} "
              f"AC@1. TEST is small (10 cases per fault), so one case is 2 points; the gain is consistent with "
              "DEV, but treat the exact numbers as rough. The engine is still far weaker on real data than on "
              "our simulator (100% there), and it abstains in "
              f"{pct(ta_['abstain_rate'])} of TEST cases.", ""]
    L += ["What is still hard. (1) Long normal periods: in the 70 minute cpu and mem cases, latency series that "
          "were near zero in the first minutes lock the baseline low, so several services stay anomalous for the "
          "whole run and form one large incident before the fault. (2) Onset order: precedence and upstream "
          "assume the root turns anomalous first. "
          + (f"With the current anomaly step the root is the first anomalous service after injection in only "
             f"{pct(d['overall']['gt_first_after_inject'])} of cases. " if d else "")
          + "(3) A cpu, mem or disk stress on one service need not slow its callers, so there may be no "
          "propagation pattern to score. (4) Windowed: when a fault starts within 5 minutes of a noise episode on "
          "an adjacent service, grouping joins it to the older incident, so no incident opens after "
          f"inject_time ({nb} of {len(res['cases'])} cases here). Our simulator spreads every fault cleanly from "
          "cause to symptoms, which is why it scores 100% there.", "",
          "Lane S answers this for the case where the failure time is known: ranking by how far each "
          "service moved from its own recent normal (the idea behind BARO) is a separate engine module "
          "(`given_time.py`), not a fifth score component, so the live engine and its contract are unchanged.", "",
          "Speed: `timeline.main_point` used to test `p in pref` on a list, which was quadratic (70 s of analyze "
          "on a 189k-event case). It is now one boolean with the same answer; that case takes about 1 s, and "
          f"all 125 cases take {res['runtime_s']:.0f} s on 7 workers.", "",
          "## Published baselines", "",
          "The RCAEval paper (arXiv 2412.17015, WWW 2025 companion) publishes one results table, Table 6, "
          "for RE2 on Train Ticket only. It has no RE1-OB AC@1 table: it points to the earlier study "
          "[6] (Pham et al., \"Root Cause Analysis for Microservice System based on Causal Inference: How "
          "Far Are We?\", ASE 2024, arXiv 2408.13729) for RE1. That study reports Avg@5 with the methods given "
          "the failure time. Its Table 5 (Online Boutique, 125 cases, Avg@5, read from the table's header "
          "order) gives BARO cpu 0.97, mem 1.00, disk 0.91, delay 0.98, loss 0.67 and NSigma 0.94, 1.00, 0.90, "
          "0.98, 0.67. Compare them with the given_time row, which uses the same information (the failure "
          "time). Our window lengths were chosen on DEV, so the all-125 per-fault numbers include tuned cases; "
          "the TEST row is the clean one.", "",
          "## Data notes", ""]
    L += [f"- {x}" for x in res["data_notes"]]
    L += ["", "## How to reproduce", "",
          "```",
          "pip install -r eval/rcaeval/requirements.txt",
          "python -I -c \"from huggingface_hub import snapshot_download; snapshot_download(repo_id='phamquiluan/RCAEval', "
          "repo_type='dataset', allow_patterns=['re1ob_*/*', 'cases.parquet', 'README.md'], local_dir='D:/rcaeval-data')\"",
          res["command"], "",
          "# Lane R protocol (DEV = repetitions 1-3, TEST = repetitions 4-5)",
          "python -I eval/rcaeval/run_rcaeval.py --split dev --tag dev_after      # tuning set",
          "python -I eval/rcaeval/run_rcaeval.py --split test --tag test_after    # run once, after freezing",
          "# Lane S: the given_time protocol is in every run above; its DEV and TEST runs are",
          "python -I eval/rcaeval/run_rcaeval.py --split dev --tag dev_given",
          "python -I eval/rcaeval/run_rcaeval.py --split test --tag test_given   # run once, after freezing",
          "# before = the same commands from a checkout of commit 6ca3443 (speed fix only), with",
          "#   --tag dev_before / --tag test_before --out <this repo>/eval/rcaeval",
          "python -I eval/rcaeval/diagnose.py --data D:/rcaeval-data", "```", "",
          f"Data: Hugging Face dataset `phamquiluan/RCAEval` (MIT licence), {res['data_files']} files, "
          f"{res['data_bytes'] / 1e6:.1f} MB on disk, stored outside the repo. Per-case answers are in "
          "`eval/rcaeval/results.json`. Generated " + res["generated_at"] + ".", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> dict:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="D:/rcaeval-data")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--limit", type=int, default=None, help="first N cases only (smoke test)")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--split", choices=sorted(SPLITS), default="all",
                    help="dev = instances 1-3 (tuning), test = instances 4-5 (run once at the end)")
    ap.add_argument("--tag", default=None,
                    help="write runs/<tag>.json (and print a summary) instead of results.json and RESULTS.md")
    ap.add_argument("--config", default=None, help="JSON dict of engine config overrides (DEV tuning only)")
    ap.add_argument("--rescore", action="store_true",
                    help="re-render RESULTS.md from the per-case answers in --out/results.json, no engine run")
    a = ap.parse_args(argv)
    data = Path(a.data)
    idx = A.read_index(data)
    n_index = len(idx)
    keep = SPLITS[a.split]
    rows_in = [r for _, r in idx.iterrows() if instance(str(r["case"])) in keep][: a.limit]
    config = json.loads(a.config) if a.config else None
    excluded, todo = [], []
    for r in rows_in:
        d = data / str(r["case"])
        if not (d / "metrics.parquet").is_file() or not (d / "inject_time.txt").is_file():
            excluded.append({"case": str(r["case"]), "why": "metrics.parquet or inject_time.txt missing"})
        else:
            todo.append((str(data), r, config))
    t0 = time.perf_counter()
    rows: list[dict] = []
    res_name = "results.json" if a.tag is None else f"runs/{a.tag}.json"
    prev = json.loads((Path(a.out) / res_name).read_text(encoding="utf-8")) if a.rescore else None
    if prev is not None:
        rows = prev["cases"]
        todo = []
    with Pool(a.workers if todo else 1) as pool:
        for i, res in enumerate(pool.imap(run_case, todo)):
            rows.append(res)
            print(f"[{i + 1}/{len(todo)}] {res['case']}: blind={res['blind']['ranking'][:1]} "
                  f"windowed={res['windowed']['ranking'][:1]} gt={res['ground_truth']} ({res['seconds']} s)",
                  file=sys.stderr, flush=True)
    rows.sort(key=lambda r: r["case"])
    by_fault = defaultdict(list)
    by_svc = defaultdict(list)
    for r in rows:
        by_fault[r["fault"]].append(r)
        by_svc[r["ground_truth"]].append(r)
    notes = []
    bad_label = [r["case"] for r in rows if not r["label_matches_dir"]]
    notes.append("Label check: cases.parquet root_cause_service equals the service in the directory name for "
                 f"{len(rows) - len(bad_label)} of {len(rows)} cases" + (f" (mismatch: {bad_label})" if bad_label else "") + ".")
    dropped: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        for c in r["dropped_columns"]:
            dropped[A.split_column(c)[0]].add(r["case"])
    for svc in sorted(dropped):
        why = A.DROPPED_SERVICES.get(svc, "not in the Online Boutique call graph")
        k = len(dropped[svc])
        where = (f"all {len(rows)} cases" if k == len(rows) else f"{k} cases" if k > 3
                 else ", ".join(f"`{c}`" for c in sorted(dropped[svc])))
        notes.append(f"Columns `{svc}_*` are not fed to the engine, present in {where}. Reason: {why}.")
    notes.append("Every other column is used, with its original name as the event name.")
    nulls = sum(r["null_values"] for r in rows)
    notes.append(f"Null cells (metric not observed at that second, skipped, no fill): {nulls}.")
    for r in rows:
        if not r["inject_in_range"]:
            notes.append(f"`{r['case']}`: inject_time {r['inject_time']} is before the first metric row "
                         "(it looks truncated in the source). Kept as is; in the windowed protocol every incident "
                         "of this case therefore qualifies.")
    for r in rows:
        if r["rows"] < 120:
            notes.append(f"`{r['case']}`: only {r['rows']} metric rows, all before inject_time (no faulty "
                         "period in the data), and it also carries Sock Shop column names (carts, catalogue, "
                         "orders, front-end). Kept and scored like every other case.")
    if excluded:
        notes.append(f"Excluded {len(excluded)} unreadable case(s): {excluded}.")
    else:
        notes.append("No case was excluded.")
    files = [p for p in data.rglob("*") if p.is_file() and ".cache" not in p.parts]
    command = prev["command"] if prev else "python -I eval/rcaeval/run_rcaeval.py" + ("" if not argv else " " + " ".join(argv))
    diag_path = Path(a.out) / "diagnostics.json"
    diag_all = json.loads(diag_path.read_text(encoding="utf-8")) if diag_path.is_file() else None
    diag = diag_all["summary"] if diag_all else None
    res = {
        "dataset": "RE1-OB", "source": "huggingface.co/datasets/phamquiluan/RCAEval (MIT licence)",
        "paper": "Pham et al., RCAEval, WWW 2025 companion, arXiv 2412.17015",
        "sentence": SOURCE_SENTENCE,
        "split": a.split, "split_instances": list(keep), "config_overrides": config,
        "n_index": n_index, "n_cases": len(rows), "n_excluded": len(excluded), "excluded": excluded,
        "weights": CONFIG["weights"], "n_topology_services": len(A.OB_SERVICES),
        "overall": {p: score(rows, p) for p in PROTOCOLS},
        "by_fault": {f: {p: score(v, p) for p in PROTOCOLS} for f, v in sorted(by_fault.items())},
        "by_service_windowed": {s: score(v, "windowed") for s, v in sorted(by_svc.items())},
        "published_baselines": None,
        "published_baselines_note": "arXiv 2412.17015 has no RE1-OB results table (only Table 6, RE2 Train Ticket).",
        "data_notes": notes, "data_files": len(files), "data_bytes": sum(p.stat().st_size for p in files),
        "command": command, "runtime_s": prev["runtime_s"] if prev else round(time.perf_counter() - t0, 1),
        "diagnostics": diag, "diagnostics_cases": diag_all["cases"] if diag_all else None,
        "generated_at": prev["generated_at"] if prev else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "cases": rows,
    }
    out = Path(a.out)
    (out / res_name).parent.mkdir(parents=True, exist_ok=True)
    (out / res_name).write_text(json.dumps({k: v for k, v in res.items() if k != "diagnostics_cases"}, indent=1) + "\n", encoding="utf-8", newline="\n")
    if a.tag is None:
        md = markdown(res, out)
        (out / "RESULTS.md").write_text(md, encoding="utf-8", newline="\n")
        print(md)
    else:
        for pr in PROTOCOLS:
            s = res["overall"][pr]
            print(f"{a.tag} {a.split} {pr}: n={s['n']} AC@1 {pct(s['ac1'])} AC@3 {pct(s['ac3'])} "
                  f"Avg@5 {pct(s['avg5'])} abstain {pct(s['abstain_rate'])} "
                  f"false blame {pct(s['false_blame_rate'])} no-inc {s['no_incident']}")
        for f, d in res["by_fault"].items():
            s = d["blind"]
            print(f"   {f}: blind AC@1 {pct(s['ac1'])} AC@3 {pct(s['ac3'])} fb {pct(s['false_blame_rate'])}"
                  f" | windowed AC@1 {pct(d['windowed']['ac1'])} fb {pct(d['windowed']['false_blame_rate'])}")
    return res


if __name__ == "__main__":
    main()
