"""Lane K diagnostics: WHY the engine scores what it scores on RE1-OB.

Runs only the engine's own step 1 (app.engine.anomaly.detect, defaults, read-only) on every
case and measures the two things the ranking depends on:
  * noise: how many services already look anomalous in the normal period (before inject_time)
  * onset order: whether the true root cause is the FIRST service to turn anomalous after
    inject_time (precedence and upstream both rest on this order)
It changes no engine code and no scores; it explains them. Writes eval/rcaeval/diagnostics.json.

    python -I eval/rcaeval/diagnose.py --data D:/rcaeval-data
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(HERE), str(ROOT / "backend"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import adapter as A  # noqa: E402
from app.engine import anomaly  # noqa: E402
from app.engine.common import parse_ts  # noqa: E402
from app.engine.config import CONFIG  # noqa: E402


def diag_case(args) -> dict:
    data_dir, row = args
    case = A.load_case(Path(data_dir), row)
    evs = sorted(case.events, key=lambda e: (e.ts, e.id))
    ts = [parse_ts(e.ts) for e in evs]
    res = anomaly.detect(evs, ts, CONFIG) if evs else anomaly.AnomalyResult({}, {})
    inj, gt = float(case.inject_time), case.ground_truth
    has_fault_period = bool(ts) and ts[-1] >= inj and inj >= ts[0]
    before, first_after, new_onsets = set(), {}, 0
    gt_ep_starts_before = None
    for svc, eps in res.episodes.items():
        for ep in eps:
            if ep.onset >= inj:
                new_onsets += 1
            for p in ep.points:
                if p.ts < inj:
                    before.add(svc)
                elif svc not in first_after or p.ts < first_after[svc][0]:
                    first_after[svc] = (p.ts, p.signal, ep.onset)
    order = sorted(first_after, key=lambda s: (first_after[s][0], s))
    t_first = first_after[order[0]][0] if order else None
    if gt in first_after:
        gt_ep_starts_before = first_after[gt][2] < inj
    return {
        "case": case.case, "fault": case.fault, "ground_truth": gt, "has_fault_period": has_fault_period,
        "services_anomalous_before_inject": len(before),
        "gt_anomalous_before_inject": gt in before,
        "gt_anomalous_after_inject": gt in first_after,
        "gt_first_after_inject": bool(order) and first_after.get(gt, (None,))[0] == t_first,
        "first_after_inject": order[:3],
        "gt_first_signal": first_after[gt][1] if gt in first_after else None,
        "gt_delay_s": round(first_after[gt][0] - inj, 1) if gt in first_after else None,
        "gt_episode_started_before_inject": gt_ep_starts_before,
        "episodes_starting_after_inject": new_onsets,
    }


def summarize(rows: list[dict]) -> dict:
    def block(rs):
        n = len(rs)
        r3 = lambda x: round(x / n, 3) if n else 0.0  # noqa: E731
        return {"n": n,
                "mean_services_anomalous_before_inject": round(sum(r["services_anomalous_before_inject"] for r in rs) / n, 2) if n else 0,
                "gt_anomalous_after_inject": r3(sum(r["gt_anomalous_after_inject"] for r in rs)),
                "gt_first_after_inject": r3(sum(r["gt_first_after_inject"] for r in rs)),
                "gt_episode_started_before_inject": r3(sum(bool(r["gt_episode_started_before_inject"]) for r in rs)),
                "no_episode_starts_after_inject": r3(sum(r["episodes_starting_after_inject"] == 0 for r in rs))}
    by = defaultdict(list)
    for r in rows:
        by[r["fault"]].append(r)
    return {"overall": block(rows), "by_fault": {f: block(v) for f, v in sorted(by.items())}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="D:/rcaeval-data")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    idx = A.read_index(Path(a.data))
    todo = [(a.data, r) for _, r in idx.iterrows()]
    with Pool(a.workers) as pool:
        rows = sorted(pool.map(diag_case, todo), key=lambda r: r["case"])
    out = {"what": "app.engine.anomaly.detect with engine defaults on every RE1-OB case; explains, does not score",
           "summary": summarize(rows), "cases": rows}
    (HERE / "diagnostics.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out["summary"], indent=1))


if __name__ == "__main__":
    main()
