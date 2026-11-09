import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.schemas import FIXTURES, Event, Topology  # noqa: E402


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def topology() -> Topology:
    return Topology.model_validate(load_fixture("topology.json"))


@pytest.fixture
def db_pool_events() -> list[Event]:
    return [Event.model_validate(e) for e in load_fixture("events_db_pool.json")]


@pytest.fixture
def ambiguous_events() -> list[Event]:
    return [Event.model_validate(e) for e in load_fixture("events_ambiguous.json")]
