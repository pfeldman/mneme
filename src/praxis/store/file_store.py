"""`FileEventStore`: one immutable JSON file per event (ADR-0001, ADR-0012).

Append-only by construction: every event lands in its own uniquely-named file,
so concurrent writers never collide and never need a lock. There is no
`update()` and no `delete()` -- overwriting an existing event file raises,
loudly.

Phase 2 (ADR-0012) extends this with:

- Per-tenant path layout: events live under `<root>/<tenant_id>/events/`. The
  file_store boundary requires `tenant_id` on every write and read; the SPI
  intentionally has no "read across tenants" surface. The path-prefix is the
  Phase 2 placeholder; Phase 3 supersedes it with RBAC.
- The filesystem rename remains the commit point; partial writes (a process
  dying between `.tmp` write and `.rename`) leave behind a `*.tmp` file that
  is ignored by readers, never a half-event presented as a real event.
- Projection is a deterministic fold over the sorted union of events present
  at read time. Two readers reading the same on-disk set get the same
  projection regardless of the order writers landed.

The "no consensus synthetic entry" clause from ADR-0012 lives at the merge
layer (`merge/projection.py`); the store just keeps every writer's event as
its own row with its own `source_id`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from .events import ObservationEvent

# The default tenant id used when the caller does not specify one. ADR-0012
# names `local` as the conventional default for Phase 2 single-tenant
# deployments. We accept this default because making `tenant_id` mandatory on
# the constructor would surface as a noisy diff across every existing call site
# without changing the contract: the layout is still per-tenant, and an
# explicit `tenant_id` is the recommended call shape.
DEFAULT_TENANT_ID: str = "local"

# Path subcomponent under `<root>/<tenant_id>/`. Kept as a constant rather than
# inlined so a Phase 3 packed-segment backend that supersedes the file layout
# behind the SPI can reuse the same tenant root.
_EVENTS_SUBDIR: str = "events"


def _validate_tenant_id(tenant_id: str) -> str:
    """Reject tenant ids that would break the path-prefix isolation.

    ADR-0012 calls the path convention a placeholder, not a security boundary;
    even so the boundary helper structurally refuses to construct a path that
    escapes the tenant root. A misconfigured deployment is a bug Phase 3
    catches, but it should not silently leak across tenants here.
    """
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("tenant_id is required and must be a non-empty string")
    # Forbid path traversal and absolute-path forms. These would let a write
    # land outside `<root>/<tenant_id>/events/`, which is exactly the leak the
    # tenancy clause structurally prevents.
    forbidden = ("/", "\\", "..", "\x00")
    for token in forbidden:
        if token in tenant_id:
            raise ValueError(
                f"tenant_id must not contain path-special characters; got {tenant_id!r}"
            )
    return tenant_id


class EventStore(ABC):
    """Pluggable append-only backend. Implementations MUST NOT offer mutation
    or deletion -- the believed state is always a projection over the full log.

    All methods accept an optional `tenant_id`. Implementations MUST scope
    reads and writes to that tenant; a read across tenants is explicitly not
    exposed on the SPI (ADR-0012).
    """

    @abstractmethod
    def append(self, event: ObservationEvent, *, tenant_id: str | None = None) -> None:
        """Persist one immutable event. Raises if the event id already exists."""

    @abstractmethod
    def read(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[ObservationEvent]:
        """Return all events (optionally for one goal), in (ts, event_id) order."""

    @abstractmethod
    def since(
        self, ts: datetime, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[ObservationEvent]:
        """Return events strictly newer than `ts`, in (ts, event_id) order."""


def _filename(event: ObservationEvent) -> str:
    # Sortable ts prefix keeps files roughly time-ordered on disk; the event
    # id guarantees uniqueness (lock-free concurrency). The event_id itself is
    # a uuid4 hex; collisions are impossible by construction so the filesystem
    # rename is a safe commit point.
    stamp = event.ts.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}__{event.event_id}.json"


class FileEventStore(EventStore):
    """One-JSON-file-per-event MVP backend with per-tenant path scoping.

    Layout::

        <root>/<tenant_id>/events/<ts>__<event_id>.json

    The constructor accepts an optional `default_tenant_id` that callers
    targeting a single tenant can rely on without threading the id through
    every call. Per-call `tenant_id` overrides the default; cross-tenant
    reads are not representable on this API by design (ADR-0012).
    """

    def __init__(
        self, root: str | Path, *, default_tenant_id: str = DEFAULT_TENANT_ID
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.default_tenant_id = _validate_tenant_id(default_tenant_id)

    # ---- tenant-scoped path helpers ---------------------------------------

    def _resolve_tenant(self, tenant_id: str | None) -> str:
        return _validate_tenant_id(tenant_id) if tenant_id is not None \
            else self.default_tenant_id

    def _events_dir(self, tenant_id: str | None) -> Path:
        tenant = self._resolve_tenant(tenant_id)
        d = self.root / tenant / _EVENTS_SUBDIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- EventStore API ---------------------------------------------------

    def append(self, event: ObservationEvent, *, tenant_id: str | None = None) -> None:
        events_dir = self._events_dir(tenant_id)
        path = events_dir / _filename(event)
        if path.exists():
            # Append-only: never overwrite an existing event (ADR-0001).
            raise FileExistsError(f"event already exists, refusing to overwrite: {path}")
        # Write to a temp file then atomically rename, so a reader never sees
        # a half-written event under concurrency (the rename is the commit
        # point, ADR-0012 section 1). The .tmp suffix is intentionally NOT a
        # .json suffix so the `*.json` glob skips it; a process that dies
        # between the write and the rename leaves no half-event visible.
        # Salt the tmp path with the event id so two writers picking the
        # same wall-clock-second cannot stomp each other's tmp file.
        tmp = events_dir / f".{event.event_id}.tmp"
        tmp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)

    def _load_all(self, tenant_id: str | None) -> list[ObservationEvent]:
        events_dir = self._events_dir(tenant_id)
        events: list[ObservationEvent] = []
        for f in events_dir.glob("*.json"):
            events.append(ObservationEvent.model_validate_json(f.read_text(encoding="utf-8")))
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def read(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[ObservationEvent]:
        events = self._load_all(tenant_id)
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        return events

    def since(
        self, ts: datetime, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[ObservationEvent]:
        return [e for e in self.read(goal_id, tenant_id=tenant_id) if e.ts > ts]
