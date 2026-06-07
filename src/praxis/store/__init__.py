"""Append-only event store (source of truth).

Knowledge is never overwritten. Each agent observation is one immutable event
appended to the log. The believed state is a *projection* (see `merge`). One file
per event (keyed by a unique id) gives lock-free concurrency (CORAL-style), full
provenance, auditability, and the ability to detect/undo poisoning. See ADR-0001.

There is intentionally NO `update()` and NO `delete()` — the only mutation is
`append()`. Storage is pluggable behind `EventStore`; `FileEventStore` is the MVP
backend (→ SQLite → Postgres+pgvector at scale).

Public API:
    ObservedSignal, ObservationEvent  -- the immutable event model
    DecayEvent                        -- projection-driven status-flip event
                                         (ADR-0013, Phase 2)
    EventStore                        -- backend interface (append/read/since
                                         + append_decay/read_decay)
    FileEventStore                    -- one-JSON-file-per-event MVP backend
"""
from __future__ import annotations

from .events import DecayEvent, ObservationEvent, ObservedSignal
from .file_store import EventStore, FileEventStore

__all__ = [
    "DecayEvent",
    "EventStore",
    "FileEventStore",
    "ObservationEvent",
    "ObservedSignal",
]
