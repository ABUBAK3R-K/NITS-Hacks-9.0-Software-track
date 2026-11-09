"""Lane B entry point. Pure and deterministic: no I/O, no clock, no LLM.

    analyze(events, topology) -> list[Incident]
"""
from app.schemas import Event, Incident, Topology


def analyze(events: list[Event], topology: Topology) -> list[Incident]:
    raise NotImplementedError("Lane B")
