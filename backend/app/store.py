"""In-memory event store + incident cache. No DB.

Owned by Lane A (append/stream) with Lane B's engine called on recompute.
Contract: events are app.schemas.Event; incidents are app.schemas.Incident.
"""
