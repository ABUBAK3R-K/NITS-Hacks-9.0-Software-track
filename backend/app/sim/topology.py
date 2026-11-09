"""Topology of the simulated payments platform.

Single source of truth: contracts/fixtures/topology.json. An edge from->to
means `from` calls `to`, so a failure on `to` flows up to `from` (its caller).
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.schemas import FIXTURES, Topology


@lru_cache(maxsize=1)
def load_topology() -> Topology:
    data = json.loads((FIXTURES / "topology.json").read_text(encoding="utf-8"))
    return Topology.model_validate(data)


def service_ids() -> list[str]:
    return [s.id for s in load_topology().services]


def deps_map() -> dict[str, list[str]]:
    """caller -> callees, in topology edge order."""
    out: dict[str, list[str]] = {}
    for e in load_topology().edges:
        out.setdefault(e.from_, []).append(e.to)
    return out


def callers_map() -> dict[str, list[str]]:
    """callee -> callers, in topology edge order."""
    out: dict[str, list[str]] = {}
    for e in load_topology().edges:
        out.setdefault(e.to, []).append(e.from_)
    return out


def callers_closure(svc: str) -> set[str]:
    """Services that transitively depend on svc."""
    cm = callers_map()
    out, stack = set(), [svc]
    while stack:
        cur = stack.pop()
        for c in cm.get(cur, []):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out
