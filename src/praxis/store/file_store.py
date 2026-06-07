"""`FileEventStore`: one immutable JSON file per event (ADR-0001).

Append-only by construction: every event lands in its own uniquely-named file, so
concurrent writers never collide and never need a lock. There is no `update()` and
no `delete()` - overwriting an existing event file raises, loudly.

Phase 2 (ADR-0013): `DecayEvent`s are stored in a sibling `decay/` subdirectory
so the existing top-level `*.json` glob still reads ObservationEvents only. The
store stays append-only for both event kinds; the projection driver decides
when to write a decay event by re-running the surviving-set diversity check.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from .events import DecayEvent, ObservationEvent


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
    def append_decay(self, event: DecayEvent) -> None:
        """Persist one immutable decay event (ADR-0013). Append-only contract
        is identical to `append()`: the file is unique by event id and never
        overwritten."""

    @abstractmethod
    def read_decay(self, goal_id: str | None = None) -> list[DecayEvent]:
        """Return all decay events (optionally for one goal), in (ts, event_id)
        order. Decay events are stored separately from observation events but
        live in the same logical log."""


def _filename(event: ObservationEvent | DecayEvent) -> str:
    # Sortable ts prefix keeps files roughly time-ordered on disk; the event id
    # guarantees uniqueness (lock-free concurrency).
    stamp = event.ts.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}__{event.event_id}.json"


class FileEventStore(EventStore):
    """MVP backend: a directory of `*.json` event files.

    Observation events live at the root; decay events live under `decay/` so
    the existing top-level glob continues to read ObservationEvents only and
    the two event kinds never deserialize against the wrong model.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.decay_root = self.root / "decay"

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

    def append_decay(self, event: DecayEvent) -> None:
        # Lazily create the subdir on first decay event so legacy stores that
        # never see a decay flip stay byte-equivalent on disk.
        self.decay_root.mkdir(parents=True, exist_ok=True)
        path = self.decay_root / _filename(event)
        if path.exists():
            raise FileExistsError(f"decay event already exists, refusing to overwrite: {path}")
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)

    def _load_all_decay(self) -> list[DecayEvent]:
        events: list[DecayEvent] = []
        if not self.decay_root.exists():
            return events
        for f in self.decay_root.glob("*.json"):
            events.append(DecayEvent.model_validate_json(f.read_text(encoding="utf-8")))
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def read_decay(self, goal_id: str | None = None) -> list[DecayEvent]:
        events = self._load_all_decay()
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        return events
