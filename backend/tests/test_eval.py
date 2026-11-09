"""Smoke test for the U3 eval harness: 5 seeds, no LLM, writes a valid EvalResults."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas import EvalResults  # noqa: E402
from eval import run_eval  # noqa: E402


def test_eval_smoke(tmp_path):
    obj = run_eval.main(["--seeds", "0-4", "--no-llm", "--out", str(tmp_path)])
    data = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    res = EvalResults.model_validate(data)
    assert res.n == 5 and res.seeds == [0, 1, 2, 3, 4]
    assert [m.name for m in res.methods] == ["jarvis", "latest_deploy", "most_alerts"]
    assert all(m.n == 5 for m in res.methods)
    assert {s.weight for s in res.sensitivity} == {"upstream", "precedence", "change", "severity"}
    assert res.command == "python eval/run_eval.py --seeds 0-4 --no-llm --out " + str(tmp_path)
    assert "llm_only not run" in res.split and "app.sim.runner" in res.split
    md = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "Evaluated on our own simulator with randomized blind faults; RCAEval real-data numbers are a " \
           "final-round item." in md
    assert chr(0x2014) not in md
    assert obj.n == 5


def test_blind_specs_are_balanced():
    from collections import Counter
    specs = run_eval.specs_for(list(range(100)))
    assert set(Counter(s.kind for s in specs).values()) == {20}
    assert run_eval.specs_for([7]) == run_eval.specs_for([7])
    assert [s.seed for s in run_eval.specs_for([50, 51])] == [50, 51]


def test_baselines_ignore_fix_applied():
    from app.schemas import Event
    ev = [Event(id="evt_1", ts="2026-11-09T08:30:00.000Z", source="sim", service="ledger-svc", kind="change",
                severity="info", name="deploy", attrs={"version": "v2"}),
          Event(id="evt_2", ts="2026-11-09T08:31:00.000Z", source="sim", service="postgres", kind="change",
                severity="info", name="fix_applied", attrs={"fix_id": "raise_pool_postgres"})]
    assert run_eval.latest_deploy(ev).top1 == "ledger-svc"
