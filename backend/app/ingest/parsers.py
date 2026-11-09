"""Upload parsers: .jsonl, .csv, or plain log lines.

Plain log line format (one per line):
    2026-11-09T08:32:05.120Z ERROR auth-svc login failed: ...
becomes kind=log, name "log", source=upload. Missing ids are assigned.
Every parser returns (events, rejected_count, errors) where errors are the
first few problems as "line N: ...".
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Callable

from pydantic import ValidationError

from app.schemas import Event

LOG_RE = re.compile(r"^(\S+)\s+(INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\s+(\S+)\s+(.*)$")
LEVELS = {"INFO": "info", "WARN": "warn", "WARNING": "warn", "ERROR": "error",
          "CRITICAL": "critical", "FATAL": "critical"}
MAX_ERRORS = 5
EVENT_FIELDS = set(Event.model_fields)

IdFn = Callable[[], str]


class _Counter:
    def __init__(self):
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"evt_up{self.n:06d}"


def norm_ts(raw: str) -> str:
    """Any ISO 8601 timestamp -> UTC, millisecond precision, trailing Z.
    Naive timestamps are taken as UTC."""
    s = raw.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        e = exc.errors()[0]
        loc = ".".join(str(x) for x in e.get("loc", ()))
        return f"{loc}: {e.get('msg')}" if loc else str(e.get("msg"))
    return str(exc)


class _Result:
    def __init__(self):
        self.events: list[Event] = []
        self.rejected = 0
        self.errors: list[str] = []

    def reject(self, lineno: int, why: str) -> None:
        self.rejected += 1
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(f"line {lineno}: {why}")

    def out(self):
        return self.events, self.rejected, self.errors


def _finish(d: dict, new_id: IdFn) -> Event:
    d = dict(d)
    if not d.get("id"):
        d["id"] = new_id()
    d.setdefault("source", "upload")
    if "ts" in d and isinstance(d["ts"], str):
        try:
            d["ts"] = norm_ts(d["ts"])
        except ValueError:
            raise ValueError(f"bad timestamp {d['ts']!r}")
    return Event.model_validate(d)


def parse_jsonl(text: str, new_id: IdFn | None = None):
    new_id = new_id or _Counter()
    r = _Result()
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("not a JSON object")
            r.events.append(_finish(obj, new_id))
        except Exception as exc:  # noqa: BLE001
            r.reject(n, _short(exc))
    return r.out()


def parse_csv(text: str, new_id: IdFn | None = None):
    """Header row with Event field names. `attrs` may be a JSON object; any
    other unknown column goes into attrs. Empty cells are treated as missing."""
    new_id = new_id or _Counter()
    r = _Result()
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        n = reader.line_num
        try:
            d: dict = {}
            attrs: dict = {}
            for k, v in row.items():
                if k is None:
                    raise ValueError("too many columns")
                k = k.strip()
                if v is None or v == "":
                    continue
                if k == "attrs":
                    attrs.update(json.loads(v))
                elif k == "value":
                    d["value"] = float(v)
                elif k in EVENT_FIELDS:
                    d[k] = v
                else:
                    attrs[k] = _num(v)
            d["attrs"] = attrs
            r.events.append(_finish(d, new_id))
        except Exception as exc:  # noqa: BLE001
            r.reject(n, _short(exc))
    return r.out()


def _num(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def parse_log_lines(text: str, new_id: IdFn | None = None):
    new_id = new_id or _Counter()
    r = _Result()
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = LOG_RE.match(line.strip())
        if not m:
            r.reject(n, "does not match '<ISO ts> <LEVEL> <service> <message>'")
            continue
        ts, level, svc, msg = m.groups()
        try:
            r.events.append(_finish({"ts": ts, "service": svc, "kind": "log", "severity": LEVELS[level],
                                     "name": "log", "message": msg}, new_id))
        except Exception as exc:  # noqa: BLE001
            r.reject(n, _short(exc))
    return r.out()


def parse_upload(filename: str, text: str, new_id: IdFn | None = None):
    """Pick a parser by extension, else sniff the first non-blank line."""
    name = (filename or "").lower()
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return parse_jsonl(text, new_id)
    if name.endswith(".csv"):
        return parse_csv(text, new_id)
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first.startswith("{"):
        return parse_jsonl(text, new_id)
    return parse_log_lines(text, new_id)
