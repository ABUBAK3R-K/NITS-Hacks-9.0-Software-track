"""U3 eval harness: blind faults from fixed seeds -> simulator -> engine and 3 baselines.

Headless, no server. Run from the repo root:

    python eval/run_eval.py --no-llm                  # jarvis + 2 heuristic baselines, seeds 0-99
    python eval/run_eval.py --seeds 50-99 --llm-n 40  # LLM baseline on the first 40 of those seeds

Every method is scored on the SAME runs. Expected root = spec.target.
  top1              the method's single answer equals the target (an abstain is never top1)
  top3              the target is in the method's top 3 ranking
  abstain_rate      share of runs where the method gave no confident answer
  false_blame_rate  confident and wrong; abstaining is never a false blame
Sensitivity: each engine weight at -50% and +50% (others unchanged), jarvis top1.

Writes eval/results.json (validates as EvalResults) and eval/RESULTS.md.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.engine.config import CONFIG  # noqa: E402
from app.engine.run import analyze  # noqa: E402
from app.schemas import EvalResults, Event, Incident  # noqa: E402
from app.sim.topology import load_topology  # noqa: E402

from app.sim.runner import blind_specs_for_eval, simulate_spec  # noqa: E402  (Lane E)

RUNNER = "app.sim.runner"


def specs_for(seeds: list[int]):
    """Blind spec per seed from Lane E's blind_specs_for_eval, which interleaves the kinds: spec i
    has seed i and kind ALL_KINDS[i % 5], so seeds 0-99 hold 20 of each kind and 0-49 / 50-99 hold
    10 of each. A seed always maps to the same spec, whatever range is asked for."""
    table = blind_specs_for_eval(n_per_kind=max(20, max(seeds) // 5 + 1), seed0=0)
    out = []
    for s in seeds:
        spec = table[s]
        assert spec.seed == s, (spec.seed, s)
        out.append(spec)
    return out


OUT_DIR = ROOT / "eval"
WEIGHTS = ("upstream", "precedence", "change", "severity")
FIX_EVENT = CONFIG["fix_event_name"]
DISCLAIMER = ("Evaluated on our own simulator with randomized blind faults; RCAEval real-data numbers are a "
              "final-round item.")


# ---------------------------------------------------------------- answers
class Answer:
    """One method's answer on one run: a ranking and whether it committed to #1."""

    def __init__(self, ranking: list[str], confident: bool):
        self.ranking = ranking
        self.confident = confident and bool(ranking)

    @property
    def top1(self):
        return self.ranking[0] if self.confident else None


def primary_incident(incs: list[Incident]) -> Incident | None:
    """The incident a responder would open first: most services, then most alerts, then earliest.
    Never uses the ground truth."""
    if not incs:
        return None
    return max(incs, key=lambda i: (len(i.services), i.alert_count, [-ord(ch) for ch in i.opened_at]))


def jarvis(events: list[Event], topo, config: dict | None = None) -> Answer:
    inc = primary_incident(analyze(events, topo, config))
    if inc is None:
        return Answer([], False)
    ranking = [c.service for c in inc.candidates]
    return Answer(ranking, inc.root_cause is not None)


def _alert_counts(events: list[Event], include_errors: bool) -> Counter:
    n: Counter = Counter()
    for e in events:
        if e.kind == "alert" or (include_errors and e.severity in ("error", "critical")):
            n[e.service] += 1
    return n


def latest_deploy(events: list[Event]) -> Answer:
    """Blame the service with the most recent change event (fix_applied excluded), else the most alerts."""
    last: dict[str, str] = {}
    for e in events:
        if e.kind == "change" and e.name != FIX_EVENT:
            if e.service not in last or e.ts > last[e.service]:
                last[e.service] = e.ts
    ranking = sorted(last, key=lambda s: (last[s], s), reverse=True)
    alerts = _alert_counts(events, include_errors=False)
    ranking += [s for s, _ in sorted(alerts.items(), key=lambda x: (-x[1], x[0])) if s not in ranking]
    return Answer(ranking, True)


def most_alerts(events: list[Event]) -> Answer:
    """Blame the service with the most alert or error events."""
    n = _alert_counts(events, include_errors=True)
    return Answer([s for s, _ in sorted(n.items(), key=lambda x: (-x[1], x[0]))], True)


def naive_available() -> bool:
    try:
        from app.narrate.naive import ask_naive  # noqa: F401  (Lane H)
    except ImportError:
        return False
    return True


# Groq free tier: 8,000 tokens/min and one naive prompt is ~4k tokens, so calls are paced.
LLM_PACE_S = 32.0
LLM_RETRIES = 4
LLM_ERRORS = {"count": 0}


async def llm_only(events: list[Event]) -> Answer:
    """Same model as the narrator, ONLY. ask_naive falls back to Gemini when Groq fails;
    in the eval that would silently mix models, so the Gemini key is removed for these
    calls and a failed Groq call is retried with backoff. A run that still has no Groq
    answer counts as "no answer": not top-1 and not a false blame, and it is counted in
    the split text."""
    from app.narrate.naive import ask_naive  # Lane H
    saved = os.environ.pop("GEMINI_API_KEY", None)
    try:
        for attempt in range(LLM_RETRIES):
            ans = await ask_naive(events, timeout_s=20)
            if ans.provider == "groq":
                root = ans.root_cause_service
                await asyncio.sleep(LLM_PACE_S)
                return Answer([root] if root else [], root is not None)
            print(f"  llm_only: groq failed (attempt {attempt + 1}): {ans.raw_text[:80]}", file=sys.stderr)
            await asyncio.sleep(LLM_PACE_S * (attempt + 1))
        LLM_ERRORS["count"] += 1
        return Answer([], False)
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY"] = saved


# ---------------------------------------------------------------- scoring
def score(name: str, answers: list[Answer], targets: list[str]) -> dict:
    n = len(answers)
    if n == 0:
        return {"name": name, "n": 0, "top1": 0.0, "top3": 0.0, "abstain_rate": 0.0, "false_blame_rate": 0.0}
    top1 = sum(a.top1 == t for a, t in zip(answers, targets))
    top3 = sum(t in a.ranking[:3] for a, t in zip(answers, targets))
    abst = sum(not a.confident for a in answers)
    fb = sum(a.confident and a.top1 != t for a, t in zip(answers, targets))
    r = lambda x: round(x / n, 3)  # noqa: E731
    return {"name": name, "n": n, "top1": r(top1), "top3": r(top3), "abstain_rate": r(abst),
            "false_blame_rate": r(fb)}


def parse_seeds(s: str) -> list[int]:
    out: list[int] = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def run(seeds: list[int], *, no_llm: bool, llm_n: int | None, sensitivity: bool = True) -> tuple[dict, dict]:
    topo = load_topology()
    runs = []
    for spec in specs_for(seeds):
        events, _ = simulate_spec(spec)
        runs.append((spec, events))
    targets = [s.target for s, _ in runs]
    answers = {"jarvis": [jarvis(ev, topo) for _, ev in runs],
               "latest_deploy": [latest_deploy(ev) for _, ev in runs],
               "most_alerts": [most_alerts(ev) for _, ev in runs]}
    methods = [score(k, v, targets) for k, v in answers.items()]
    llm_seeds: list[int] = []
    if not no_llm and not naive_available():
        print("llm_only skipped: app.narrate.naive (Lane H) is not importable in this tree. "
              "Run again after the merge, or pass --no-llm.", file=sys.stderr)
        no_llm = True
    if not no_llm:
        k = len(runs) if llm_n is None else min(llm_n, len(runs))
        llm_seeds = seeds[:k]

        async def all_llm():
            out = []
            for i, (_, ev) in enumerate(runs[:k]):
                out.append(await llm_only(ev))
                print(f"  llm_only {i + 1}/{k}", file=sys.stderr, flush=True)
            return out
        llm_ans = asyncio.run(all_llm())
        answers["llm_only"] = llm_ans
        methods.append(score("llm_only", llm_ans, targets[:k]))

    sens = []
    if sensitivity:
        for w in WEIGHTS:
            row = {"weight": w}
            for label, f in (("minus50_top1", 0.5), ("plus50_top1", 1.5)):
                weights = dict(CONFIG["weights"])
                weights[w] = round(weights[w] * f, 4)
                got = [jarvis(ev, topo, {"weights": weights}) for _, ev in runs]
                row[label] = score("jarvis", got, targets)["top1"]
            sens.append(row)

    # per-kind breakdown and misses, for RESULTS.md only
    by_kind: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for i, (spec, _) in enumerate(runs):
        for name, ans in answers.items():
            if i < len(ans):
                by_kind[spec.kind][name].append(ans[i].top1 == spec.target)
    extra = {"by_kind": {k: {m: (sum(v), len(v)) for m, v in d.items()} for k, d in sorted(by_kind.items())},
             "no_incident": sum(1 for a in answers["jarvis"] if not a.ranking),
             "kinds": Counter(s.kind for s, _ in runs), "llm_seeds": llm_seeds,
             "decoys": sum(1 for s, _ in runs if s.decoy is not None)}
    return {"methods": methods, "sensitivity": sens}, extra


def seed_label(seeds: list[int]) -> str:
    if seeds and seeds == list(range(seeds[0], seeds[-1] + 1)):
        return f"{seeds[0]}-{seeds[-1]}"
    return ",".join(map(str, seeds))


def write(seeds: list[int], res: dict, extra: dict, command: str, split_note: str, out_dir: Path) -> EvalResults:
    llm = [m for m in res["methods"] if m["name"] == "llm_only"]
    split = (f"Weights frozen before eval (engine defaults {CONFIG['weights']}). Blind specs (one per seed, kind balanced by seed) "
             f"for seeds {seed_label(seeds)} ({len(seeds)} runs: "
             + ", ".join(f"{v} {k}" for k, v in sorted(extra["kinds"].items()))
             + f"; {extra['decoys']} with a decoy deploy), runner {RUNNER}, each run 60 s baseline + 90 s after "
             f"injection. All methods scored on the same runs; expected root = spec.target. "
             + (f"llm_only scored on the first {len(extra['llm_seeds'])} of these seeds "
                f"({seed_label(extra['llm_seeds'])}), Groq {os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')} only "
                f"(no Gemini fallback), temperature 0, paced for the 8k tokens/min limit; "
                f"{LLM_ERRORS['count']} run(s) got no Groq answer after retries and count as no answer. " if llm else "llm_only not run. ")
             + split_note).strip()
    obj = EvalResults.model_validate({
        "n": len(seeds), "seeds": seeds, "split": split, "methods": res["methods"],
        "sensitivity": res["sensitivity"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "command": command})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(obj.model_dump(mode="json"), indent=2) + "\n",
                                          encoding="utf-8", newline="\n")
    (out_dir / "RESULTS.md").write_text(markdown(obj, extra), encoding="utf-8", newline="\n")
    return obj


LABELS = {"jarvis": "J.A.R.V.I.S.", "latest_deploy": "Latest deploy", "most_alerts": "Most alerts",
          "llm_only": "LLM only"}


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def markdown(r: EvalResults, extra: dict) -> str:
    L = ["# Eval results (U3)", "", DISCLAIMER, "",
         f"{r.n} blind runs, seeds {seed_label(r.seeds)}.", "",
         "| Method | n | Top-1 | Top-3 | Abstain | False blame |", "|---|---|---|---|---|---|"]
    for m in r.methods:
        L.append(f"| {LABELS.get(m.name, m.name)} | {m.n} | {pct(m.top1)} | {pct(m.top3)} | "
                 f"{pct(m.abstain_rate)} | {pct(m.false_blame_rate)} |")
    L += ["", "False blame = confident and wrong. Abstaining is never a false blame, and never a top-1 hit.", ""]
    if r.sensitivity:
        L += ["## Weight sensitivity (J.A.R.V.I.S. top-1)", "",
              "| Weight | -50% | default | +50% |", "|---|---|---|---|"]
        base = next(m.top1 for m in r.methods if m.name == "jarvis")
        for s in r.sensitivity:
            L.append(f"| {s.weight} | {pct(s.minus50_top1)} | {pct(base)} | {pct(s.plus50_top1)} |")
        L.append("")
    L += ["## Top-1 by fault kind", "", "| Kind | " + " | ".join(LABELS.get(m.name, m.name) for m in r.methods) + " |",
          "|---|" + "---|" * len(r.methods)]
    for kind, d in extra["by_kind"].items():
        cells = []
        for m in r.methods:
            hit, n = d.get(m.name, (0, 0))
            cells.append(f"{hit}/{n}")
        L.append(f"| {kind} | " + " | ".join(cells) + " |")
    L += ["", f"Runs where the engine opened no incident at all: {extra['no_incident']} (counted as misses).", "",
          "## How it was run", "", r.split, "", "Reproduce from the repo root:", "", "```", r.command, "```", "",
          "## README", "",
          "- `eval/run_eval.py` is headless: blind FaultSpec per seed, simulator, then the engine and the "
          "baselines on the same events. No server, no clock, deterministic for a fixed seed list.",
          "- Baselines: `latest_deploy` blames the service with the most recent change event (fix_applied "
          "excluded), else the most alerts; `most_alerts` blames the service with the most alert or "
          "error-severity events; `llm_only` asks the frozen naive prompt (app.narrate.naive.ask_naive) for one "
          "service, so its top-3 equals its top-1 and a missing answer counts as an abstain.",
          "- J.A.R.V.I.S. is scored on the incident a responder would open first (most services, then most "
          "alerts); ground truth is never used to pick it. ABSTAINED counts as not top-1 and not a false blame.",
          "- Flags: `--no-llm`, `--llm-n N` (LLM on the first N seeds of the list, reported as its own n), "
          "`--seeds a-b`, `--no-sensitivity`.",
          "- Honesty rule: weights are frozen before the final eval; any tuning uses seeds 0-49 only and the "
          "reported numbers then come from seeds 50-99 only (see DECISIONS.md).", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> EvalResults:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", default="0-99")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-n", type=int, default=None)
    ap.add_argument("--no-sensitivity", action="store_true")
    ap.add_argument("--split-note", default="", help="extra text for the split field")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    seeds = parse_seeds(a.seeds)
    res, extra = run(seeds, no_llm=a.no_llm, llm_n=a.llm_n, sensitivity=not a.no_sensitivity)
    command = "python eval/run_eval.py" + ("" if not argv else " " + " ".join(argv))
    obj = write(seeds, res, extra, command, a.split_note, Path(a.out))
    print(markdown(obj, extra))
    return obj


if __name__ == "__main__":
    main()
