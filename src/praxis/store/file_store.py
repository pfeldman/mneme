"""`FileEventStore`: one immutable JSON file per event (ADR-0001).

Append-only by construction: every event lands in its own uniquely-named file, so
concurrent writers never collide and never need a lock. There is no `update()` and
no `delete()` - overwriting an existing event file raises, loudly.

Phase 2 (ADR-0014) widens the backend to also persist `CandidateEvent`. Candidate
files use a `cand_` filename prefix so the read paths stay disjoint: signal events
flow through `read()` / `since()`, candidates through `read_candidates()` /
`candidates_since()`. The diversity gate (`oracle/trust.py`) only sees signal
events through the projection - a candidate write cannot be miscounted as
evidence (ADR-0008 schema-drift defense).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from .events import CandidateEvent, ObservationEvent

# Files named with this prefix carry CandidateEvent payloads, NOT ObservationEvent
# payloads. The prefix keeps the legacy `*.json` glob for ObservationEvent unchanged
# while letting both types coexist in one directory.
CANDIDATE_FILE_PREFIX = "cand_"


class EventStore(ABC):
    """Pluggable append-only backend. Implementations MUST NOT offer mutation or
    deletion - the believed state is always a projection over the full log."""

    @abstractmethod
    def append(self, event: ObservationEvent) -> None:
        """Persist one immutable event. Raises if the event id already exists."""

    @abstractmethod
    def read(self, goal_id: str | None = None) -> list[ObservationEvent]:
        """Return all events (optionally for one goal), in (ts, event_id) order."""

    @abstractmethod
    def since(self, ts: datetime, goal_id: str | None = None) -> list[ObservationEvent]:
        """Return events strictly newer than `ts`, in (ts, event_id) order."""

    @abstractmethod
    def append_candidate(self, event: CandidateEvent) -> None:
        """Persist one immutable candidate event (ADR-0014). Raises on overwrite."""

    @abstractmethod
    def read_candidates(self, goal_id: str | None = None) -> list[CandidateEvent]:
        """Return all candidate events (optionally for one goal), time-ordered."""

    @abstractmethod
    def candidates_since(
        self, ts: datetime, goal_id: str | None = None,
    ) -> list[CandidateEvent]:
        """Return candidate events strictly newer than `ts`, time-ordered."""


def _filename(event: ObservationEvent) -> str:
    # Sortable ts prefix keeps files roughly time-ordered on disk; the event id
    # guarantees uniqueness (lock-free concurrency).
    stamp = event.ts.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}__{event.event_id}.json"


def _candidate_filename(event: CandidateEvent) -> str:
    # The `cand_` prefix keeps candidate files glob-disjoint from signal events
    # so neither read path picks up the other type (ADR-0014).
    stamp = event.ts.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{CANDIDATE_FILE_PREFIX}{stamp}__{event.event_id}.json"


class FileEventStore(EventStore):
    """MVP backend: a directory of `*.json` event files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- ObservationEvent ----------------------------------------------------

    def append(self, event: ObservationEvent) -> None:
        path = self.root / _filename(event)
        if path.exists():
            # Append-only: never overwrite an existing event (ADR-0001).
            raise FileExistsError(f"event already exists, refusing to overwrite: {path}")
        # Write to a temp file then atomically rename, so a reader never sees a
        # half-written event under concurrency.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)

    def _load_all(self) -> list[ObservationEvent]:
        events: list[ObservationEvent] = []
        for f in self.root.glob("*.json"):
            # Skip candidate files: they live in the same dir but with the
            # `cand_` prefix and a different shape.
            if f.name.startswith(CANDIDATE_FILE_PREFIX):
                continue
            events.append(ObservationEvent.model_validate_json(f.read_text(encoding="utf-8")))
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def read(self, goal_id: str | None = None) -> list[ObservationEvent]:
        events = self._load_all()
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        return events

    def since(self, ts: datetime, goal_id: str | None = None) -> list[ObservationEvent]:
        return [e for e in self.read(goal_id) if e.ts > ts]

    # --- CandidateEvent (ADR-0014) -------------------------------------------

    def append_candidate(self, event: CandidateEvent) -> None:
        path = self.root / _candidate_filename(event)
        if path.exists():
            # Append-only: candidates are immutable too; human promotion appends
            # a fresh seed event, never edits this one (ADR-0014 sec 4).
            raise FileExistsError(
                f"candidate event already exists, refusing to overwrite: {path}"
            )
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)

    def _load_all_candidates(self) -> list[CandidateEvent]:
        events: list[CandidateEvent] = []
        for f in self.root.glob(f"{CANDIDATE_FILE_PREFIX}*.json"):
            events.append(CandidateEvent.model_validate_json(f.read_text(encoding="utf-8")))
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def read_candidates(self, goal_id: str | None = None) -> list[CandidateEvent]:
        events = self._load_all_candidates()
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        return events

    def candidates_since(
        self, ts: datetime, goal_id: str | None = None,
    ) -> list[CandidateEvent]:
        return [e for e in self.read_candidates(goal_id) if e.ts > ts]
