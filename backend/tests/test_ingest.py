"""Upload parsers: jsonl, csv, plain log lines, malformed lines rejected."""
import json
from pathlib import Path

from app.ingest.parsers import norm_ts, parse_csv, parse_jsonl, parse_log_lines, parse_upload

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "payment_outage.log"


def test_log_lines():
    text = ("2026-11-09T08:32:05.120Z ERROR auth-svc login failed: pool timeout\n"
            "\n"
            "2026-11-09T08:32:06Z FATAL postgres FATAL: remaining connection slots are reserved\n"
            "this line is garbage\n"
            "2026-11-09T08:32:07.5+05:30 WARNING ledger-svc slow write\n"
            "not-a-time INFO x y\n")
    evs, rej, errs = parse_log_lines(text)
    assert len(evs) == 3 and rej == 2
    assert errs[0].startswith("line 4:") and errs[1].startswith("line 6:")
    e = evs[0]
    assert (e.kind, e.name, e.source, e.severity, e.service) == ("log", "log", "upload", "error", "auth-svc")
    assert e.message == "login failed: pool timeout" and e.id.startswith("evt_")
    assert evs[1].severity == "critical" and evs[1].ts == "2026-11-09T08:32:06.000Z"
    assert evs[2].severity == "warn" and evs[2].ts == "2026-11-09T03:02:07.500Z"
    assert len({x.id for x in evs}) == 3


def test_jsonl():
    good = {"ts": "2026-11-09T08:32:05.120Z", "service": "postgres", "kind": "metric", "severity": "critical",
            "name": "db_pool_in_use_pct", "value": 100, "attrs": {"pool_max": 100}}
    with_id = dict(good, id="evt_abc", source="api")
    text = "\n".join([json.dumps(good), "{not json", json.dumps(with_id),
                      json.dumps(dict(good, kind="nope")), "[1,2]"])
    evs, rej, errs = parse_jsonl(text)
    assert len(evs) == 2 and rej == 3 and len(errs) == 3
    assert evs[0].source == "upload" and evs[0].id.startswith("evt_")
    assert evs[1].id == "evt_abc" and evs[1].source == "api"
    assert errs[0].startswith("line 2:")


def test_csv():
    text = ("ts,service,kind,severity,name,value,message,user_id\n"
            "2026-11-09T08:32:05.120Z,auth-svc,request,error,http_request,,POST /v1/login 503,u_1\n"
            "2026-11-09T08:32:06.000Z,postgres,metric,critical,db_pool_in_use_pct,100,,\n"
            "2026-11-09T08:32:07.000Z,postgres,metric,bogus,x,1,,\n"
            "2026-11-09T08:32:08.000Z,postgres,metric,info,x,notanumber,,\n")
    evs, rej, errs = parse_csv(text)
    assert len(evs) == 2 and rej == 2
    assert evs[0].attrs == {"user_id": "u_1"} and evs[0].value is None
    assert evs[1].value == 100.0
    assert errs[0].startswith("line 4:")


def test_parse_upload_dispatch_and_sample_file():
    text = SAMPLE.read_text(encoding="utf-8")
    evs, rej, _ = parse_upload("payment_outage.log", text)
    assert rej == 0 and 80 <= len(evs) <= 150
    svcs = {e.service for e in evs if e.severity in ("error", "critical")}
    assert {"postgres", "auth-svc", "ledger-svc", "payments-svc", "api-gateway"} <= svcs
    first_err = next(e for e in evs if e.severity in ("error", "critical"))
    assert first_err.service == "postgres"
    jl = parse_upload("x.txt", '{"ts":"2026-11-09T08:00:00Z","service":"a","kind":"log","severity":"info","name":"log"}')
    assert len(jl[0]) == 1


def test_norm_ts():
    assert norm_ts("2026-11-09T08:32:05.123456Z") == "2026-11-09T08:32:05.123Z"
    assert norm_ts("2026-11-09 08:32:05") == "2026-11-09T08:32:05.000Z"
