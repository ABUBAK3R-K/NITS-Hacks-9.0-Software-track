"""Grounding rule 4: a causal claim between services needs a dependency path in the topology."""
import pytest

from app.narrate.grounding import causal_claims, ground, impossible_causal_claim
from app.schemas import Incident
from app.sim.topology import service_ids
from tests.conftest import load_fixture

CITE = ["evt_000790"]  # in the hero incident's evidence set


@pytest.fixture
def hero() -> Incident:
    return Incident.model_validate(load_fixture("incident_db_pool.json"))


@pytest.mark.parametrize("text", [
    "postgres pool exhaustion caused auth-svc login failures.",
    "postgres saturation led to slow api-gateway responses.",
    "Because of postgres, ledger-svc commits timed out.",
    "api-gateway latency was caused by postgres.",
    "payments-svc failed because ledger-svc returned 503.",
    "The ledger-svc deploy did not cause the auth-svc failures.",
    # template "why not" wording: a bare "because" explains a score, it is not a causal claim
    "psp-adapter scores 0.800 vs 0.128 because api-gateway is downstream of psp-adapter failures.",
])
def test_possible_or_negated_claims_are_kept(text):
    assert impossible_causal_claim(text) is None


@pytest.mark.parametrize("text,pair", [
    ("ledger-svc errors led to auth-svc login failures.", ("ledger-svc", "auth-svc")),
    ("auth-svc failed because of ledger-svc errors.", ("ledger-svc", "auth-svc")),
    ("Because of redis-session, payments-svc broke.", ("redis-session", "payments-svc")),
    ("api-gateway timeouts caused postgres pool exhaustion.", ("api-gateway", "postgres")),
])
def test_impossible_claims_are_found(text, pair):
    assert impossible_causal_claim(text) == pair


def test_ground_keeps_valid_and_drops_impossible(hero):
    raw = [
        {"text": "postgres pool exhaustion caused auth-svc login failures.", "cites": CITE},
        {"text": "ledger-svc errors led to auth-svc login failures.", "cites": CITE},
    ]
    kept, dropped = ground(raw, hero)
    assert dropped == 1
    assert [s.text for s in kept] == ["postgres pool exhaustion caused auth-svc login failures."]


def test_sentence_without_causal_phrase_is_untouched():
    assert causal_claims("auth-svc and ledger-svc both show errors.", service_ids()) == []
