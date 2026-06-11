"""`FileEventStore`: one immutable JSON file per event (ADR-0001, ADR-0012, ADR-0013, ADR-0014).

Append-only by construction: every event lands in its own uniquely-named file,
so concurrent writers never collide and never need a lock. There is no
`update()` and no `delete()` - overwriting an existing event file raises,
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

Phase 2 (ADR-0013) extends this with:

- `DecayEvent`s are stored in a sibling `decay/` subdirectory under the same
  tenant root (`<root>/<tenant_id>/decay/`), so the existing observation-event
  `*.json` glob continues to read ObservationEvents only and the two event
  kinds never deserialize against the wrong model. The store stays
  append-only for both event kinds; the projection driver decides when to
  write a decay event by re-running the surviving-set diversity check.

Phase 2 (ADR-0014) extends this with:

- `CandidateEvent`s are stored in a sibling `candidates/` subdirectory under
  the same tenant root (`<root>/<tenant_id>/candidates/`). The read paths stay
  disjoint: signal events flow through `read()` / `since()`, decay events
  through `read_decay()`, candidates through `read_candidates()` /
  `candidates_since()`. The diversity gate (`oracle/trust.py`) only sees
  signal events through the projection - a candidate write cannot be
  miscounted as evidence (ADR-0008 schema-drift defense).

The "no consensus synthetic entry" clause from ADR-0012 lives at the merge
layer (`merge/projection.py`); the store just keeps every writer's event as
its own row with its own `source_id`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from .events import (
    CandidateEvent,
    DecayEvent,
    ObservationEvent,
    RegressObservationEvent,
)

# The default tenant id used when the caller does not specify one. ADR-0012
# names `local` as the conventional default for Phase 2 single-tenant
# deployments. We accept this default because making `tenant_id` mandatory on
# the constructor would surface as a noisy diff across every existing call site
# without changing the contract: the layout is still per-tenant, and an
# explicit `tenant_id` is the recommended call shape.
DEFAULT_TENANT_ID: str = "local"

# Path subcomponents under `<root>/<tenant_id>/`. Kept as constants rather than
# inlined so a Phase 3 packed-segment backend that supersedes the file layout
# behind the SPI can reuse the same tenant root.
_EVENTS_SUBDIR: str = "events"
_DECAY_SUBDIR: str = "decay"
_CANDIDATES_SUBDIR: str = "candidates"
# ADR-0023 decision 4 traceability: regress observation records live in their
# own sibling subdir so the believed-state projection's glob over `events/`
# never reads them. They are NON-PROMOTABLE by construction (ADR-0029): a
# record here can never grow the believed set.
_REGRESS_SUBDIR: str = "regress"


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

    @abstractmethod
    def append_decay(
        self, event: DecayEvent, *, tenant_id: str | None = None
    ) -> None:
        """Persist one immutable decay event (ADR-0013). Append-only contract
        is identical to `append()`: the file is unique by event id and never
        overwritten."""

    @abstractmethod
    def read_decay(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[DecayEvent]:
        """Return all decay events (optionally for one goal), in (ts, event_id)
        order. Decay events are stored separately from observation events but
        live in the same logical log."""

    @abstractmethod
    def append_regress(
        self, event: RegressObservationEvent, *, tenant_id: str | None = None
    ) -> None:
        """Persist one immutable regress observation record (ADR-0023 decision 4).

        Append-only contract is identical to `append()`. This record is NEVER
        read by the projection or the oracle gate (it is non-promotable by
        construction, ADR-0029); it is the traceable audit trail of what a
        regress run observed for a goal that reached a verdict."""

    @abstractmethod
    def read_regress(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[RegressObservationEvent]:
        """Return all regress observation records (optionally for one goal), in
        (ts, event_id) order. Stored separately from observation events so the
        believed projection never folds them into belief (ADR-0029)."""

    @abstractmethod
    def append_candidate(
        self, event: CandidateEvent, *, tenant_id: str | None = None
    ) -> None:
        """Persist one immutable candidate event (ADR-0014). Raises on overwrite.

        Append-only contract is identical to `append()`: human promotion
        appends a fresh seed event, never edits the original (ADR-0014 sec 4).
        """

    @abstractmethod
    def read_candidates(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[CandidateEvent]:
        """Return all candidate events (optionally for one goal), time-ordered."""

    @abstractmethod
    def candidates_since(
        self,
        ts: datetime,
        goal_id: str | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[CandidateEvent]:
        """Return candidate events strictly newer than `ts`, time-ordered."""


def _filename(
    event: ObservationEvent | DecayEvent | CandidateEvent | RegressObservationEvent,
) -> str:
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
        <root>/<tenant_id>/decay/<ts>__<event_id>.json       (ADR-0013)
        <root>/<tenant_id>/candidates/<ts>__<event_id>.json  (ADR-0014)

    Observation events live under `events/`; decay events live under a sibling
    `decay/` subdirectory; candidate events live under a sibling `candidates/`
    subdirectory. The three event kinds never deserialize against the wrong
    model because each glob is scoped to its own subdirectory.

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

    def _decay_dir(self, tenant_id: str | None) -> Path:
        tenant = self._resolve_tenant(tenant_id)
        d = self.root / tenant / _DECAY_SUBDIR
        # Lazily create the subdir on first decay event so legacy stores that
        # never see a decay flip stay byte-equivalent on disk.
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _candidates_dir(self, tenant_id: str | None) -> Path:
        tenant = self._resolve_tenant(tenant_id)
        d = self.root / tenant / _CANDIDATES_SUBDIR
        # Lazily created on first candidate write; absent dir = no candidates
        # for this tenant (legacy stores stay byte-equivalent on disk).
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _regress_dir(self, tenant_id: str | None) -> Path:
        tenant = self._resolve_tenant(tenant_id)
        d = self.root / tenant / _REGRESS_SUBDIR
        # Lazily created on first regress record; absent dir = no regress runs
        # for this tenant (legacy stores stay byte-equivalent on disk).
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- EventStore API: ObservationEvent ---------------------------------

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

    # ---- EventStore API: DecayEvent (ADR-0013) ----------------------------

    def append_decay(
        self, event: DecayEvent, *, tenant_id: str | None = None
    ) -> None:
        decay_dir = self._decay_dir(tenant_id)
        path = decay_dir / _filename(event)
        if path.exists():
            raise FileExistsError(
                f"decay event already exists, refusing to overwrite: {path}"
            )
        # Same atomic-rename commit as `append()`. Salt the tmp name with the
        # event id so concurrent decay writers do not stomp each other.
        tmp = decay_dir / f".{event.event_id}.tmp"
        tmp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)

    def _load_all_decay(self, tenant_id: str | None) -> list[DecayEvent]:
        tenant = self._resolve_tenant(tenant_id)
        decay_dir = self.root / tenant / _DECAY_SUBDIR
        events: list[DecayEvent] = []
        if not decay_dir.exists():
            return events
        for f in decay_dir.glob("*.json"):
            events.append(DecayEvent.model_validate_json(f.read_text(encoding="utf-8")))
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def read_decay(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[DecayEvent]:
        events = self._load_all_decay(tenant_id)
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        return events

    # ---- EventStore API: RegressObservationEvent (ADR-0023) ---------------

    def append_regress(
        self, event: RegressObservationEvent, *, tenant_id: str | None = None
    ) -> None:
        regress_dir = self._regress_dir(tenant_id)
        path = regress_dir / _filename(event)
        if path.exists():
            raise FileExistsError(
                f"regress event already exists, refusing to overwrite: {path}"
            )
        # Same atomic-rename commit as `append()`. Salt the tmp name with the
        # event id so concurrent regress writers (the `--jobs` aggregate path)
        # do not stomp each other's tmp file: each worker writes its own
        # uniquely-named file, lock-free (ADR-0001, ADR-0012).
        tmp = regress_dir / f".{event.event_id}.tmp"
        tmp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)

    def _load_all_regress(self, tenant_id: str | None) -> list[RegressObservationEvent]:
        tenant = self._resolve_tenant(tenant_id)
        regress_dir = self.root / tenant / _REGRESS_SUBDIR
        events: list[RegressObservationEvent] = []
        if not regress_dir.exists():
            return events
        for f in regress_dir.glob("*.json"):
            events.append(
                RegressObservationEvent.model_validate_json(
                    f.read_text(encoding="utf-8")
                )
            )
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def read_regress(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[RegressObservationEvent]:
        events = self._load_all_regress(tenant_id)
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        return events

    # ---- EventStore API: CandidateEvent (ADR-0014) ------------------------

    def append_candidate(
        self, event: CandidateEvent, *, tenant_id: str | None = None
    ) -> None:
        candidates_dir = self._candidates_dir(tenant_id)
        path = candidates_dir / _filename(event)
        if path.exists():
            # Append-only: candidates are immutable too; human promotion appends
            # a fresh seed event, never edits this one (ADR-0014 sec 4).
            raise FileExistsError(
                f"candidate event already exists, refusing to overwrite: {path}"
            )
        # Same atomic-rename commit as `append()`. Salt the tmp name with the
        # event id so concurrent candidate writers do not stomp each other.
        tmp = candidates_dir / f".{event.event_id}.tmp"
        tmp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(path)

    def _load_all_candidates(self, tenant_id: str | None) -> list[CandidateEvent]:
        tenant = self._resolve_tenant(tenant_id)
        candidates_dir = self.root / tenant / _CANDIDATES_SUBDIR
        events: list[CandidateEvent] = []
        if not candidates_dir.exists():
            return events
        for f in candidates_dir.glob("*.json"):
            events.append(CandidateEvent.model_validate_json(f.read_text(encoding="utf-8")))
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def read_candidates(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[CandidateEvent]:
        events = self._load_all_candidates(tenant_id)
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        return events

    def candidates_since(
        self,
        ts: datetime,
        goal_id: str | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[CandidateEvent]:
        return [e for e in self.read_candidates(goal_id, tenant_id=tenant_id) if e.ts > ts]


# Default name of the per-run directory parent under `.praxis/`. ADR-0021
# decision 1 fixes `runs/<timestamp>/` as the per-machine append-only event log
# location; the timestamped run dir is the FileEventStore root for one run.
RUNS_SUBDIR: str = "runs"


def new_run_id(now: datetime | None = None) -> str:
    """Return a sortable timestamp id for one `runs/<timestamp>/` directory.

    UTC, second resolution plus microseconds, so two runs in the same second do
    not collide and the lexical sort over run dirs matches chronological order.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
    return stamp


class RunsEventStore(FileEventStore):
    """Per-machine append-only event log spread across `runs/<timestamp>/` dirs.

    ADR-0021 decision 1 puts the raw per-machine event log under
    `.praxis/runs/<timestamp>/`, one subtree per teach / regress / explore
    session, gitignored and regenerable (decision 2). ADR-0021 decision 3 keeps
    that log the local source of truth and folds it into believed / contested
    state through the projection (`merge`), exactly as in Phase 1 and Phase 2.

    This store reconciles the file-per-event layout of `FileEventStore`
    (ADR-0012) with that runs convention:

    - WRITES land in the CURRENT run directory
      (`<runs_root>/<run_id>/<tenant>/{events,decay,candidates}/`). One run's
      writes are one append-only subtree; nothing is ever mutated in place
      (ADR-0001), and the file-per-event id keeps concurrent writers lock-free
      (ADR-0012).
    - READS fold across EVERY `<runs_root>/<timestamp>/` subtree (the current
      run and all prior runs). The projection therefore sees the whole
      per-machine log, so the believed-state projection works unchanged across
      separate CLI invocations: an `explore` that wrote a candidate in one run
      dir is visible to a later `review` reading across all run dirs.

    The base `FileEventStore` is reused for both halves: the current-run root is
    a plain `FileEventStore`, and each prior-run subtree is read through a
    throwaway `FileEventStore` rooted at that subtree. No base-class behavior is
    overridden except to widen reads from one root to the union of run roots.
    """

    def __init__(
        self,
        runs_root: str | Path,
        run_id: str,
        *,
        default_tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self.runs_root = Path(runs_root)
        self.run_id = run_id
        # The current-run subtree is the write target; the base class manages it
        # as an ordinary one-file-per-event root.
        super().__init__(
            self.runs_root / run_id, default_tenant_id=default_tenant_id
        )

    def _run_roots(self) -> list[Path]:
        """Every existing `runs/<timestamp>/` subtree, current run last.

        The current run's root is always present (the base constructor created
        it). Prior runs are any sibling timestamp dirs; sorting by name matches
        chronological order because `new_run_id` is a sortable UTC stamp.
        """
        if not self.runs_root.exists():
            return [self.root]
        roots = [
            p for p in sorted(self.runs_root.iterdir())
            if p.is_dir() and p != self.root
        ]
        roots.append(self.root)
        return roots

    def _read_across(
        self, method: str, *args: object, tenant_id: str | None, **kwargs: object
    ) -> (
        list[ObservationEvent]
        | list[DecayEvent]
        | list[CandidateEvent]
        | list[RegressObservationEvent]
    ):
        """Call a base read method on every run root and concat, time-ordered.

        Re-sorts the union by (ts, event_id) so the cross-run fold is the same
        deterministic order a single-root projection would see (ADR-0012: two
        readers of the same on-disk set get the same projection).
        """
        merged: list[object] = []
        for root in self._run_roots():
            sub = FileEventStore(root, default_tenant_id=self.default_tenant_id)
            merged.extend(getattr(sub, method)(*args, tenant_id=tenant_id, **kwargs))
        merged.sort(key=lambda e: (e.ts, e.event_id))  # type: ignore[attr-defined]
        return merged  # type: ignore[return-value]

    def read(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[ObservationEvent]:
        return self._read_across("read", goal_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def since(
        self, ts: datetime, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[ObservationEvent]:
        return [e for e in self.read(goal_id, tenant_id=tenant_id) if e.ts > ts]

    def read_decay(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[DecayEvent]:
        return self._read_across("read_decay", goal_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def read_candidates(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[CandidateEvent]:
        return self._read_across("read_candidates", goal_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def read_regress(
        self, goal_id: str | None = None, *, tenant_id: str | None = None
    ) -> list[RegressObservationEvent]:
        return self._read_across("read_regress", goal_id, tenant_id=tenant_id)  # type: ignore[return-value]

    def candidates_since(
        self,
        ts: datetime,
        goal_id: str | None = None,
        *,
        tenant_id: str | None = None,
    ) -> list[CandidateEvent]:
        return [e for e in self.read_candidates(goal_id, tenant_id=tenant_id) if e.ts > ts]
