"""Append-only event store (source of truth).

Knowledge is never overwritten. Each agent observation is one immutable event
appended to the log. The believed state is a *projection* (see `merge`). One
file per event (keyed by a unique id) gives lock-free concurrency (CORAL-
style), full provenance, auditability, and the ability to detect/undo
poisoning. See ADR-0001 (append-only) and ADR-0012 (multi-writer concurrency
contract).

There is intentionally NO `update()` and NO `delete()` - the only mutation is
`append()`. Storage is pluggable behind `EventStore`; `FileEventStore` is the
MVP backend.

Phase 2 (ADR-0012):

- Per-tenant path layout `<root>/<tenant_id>/events/<event_id>` enforced at
  the file_store boundary. The SPI has no cross-tenant read surface.
- `source_id` is `agent_identity` (model + prompt lineage), NEVER `pid`,
  `session_id`, or any per-process token. `AgentIdentity` / `source_id_for`
  are the canonical way to construct that string.

Phase 2 (ADR-0013):

- `DecayEvent` is a sibling immutable event kind written by the projection
  driver when recency decay flips a signal's status. Stored in a sibling
  `decay/` subdirectory under each tenant so the existing `*.json` glob
  for observation events stays unaffected.

Phase 2 (ADR-0014):

- `CandidateEvent` is a sibling event type for agent-proposed risks and
  uncertainties. Stored in a sibling `candidates/` subdirectory under each
  tenant so the diversity gate over signal observations cannot count
  candidate writes as evidence (ADR-0008 schema-drift defense).

Public API:
    ObservedSignal, ObservationEvent  -- the immutable signal-event model
    DecayEvent                        -- projection-driven status-flip event
                                         (ADR-0013, Phase 2)
    CandidateEvent, CandidateRiskPayload, CandidateUncertaintyPayload,
    CandidatePayload                  -- ADR-0014 candidate event types
    EventStore                        -- backend interface (append/read/since
                                         + append_decay/read_decay
                                         + append_candidate/read_candidates)
    FileEventStore                    -- per-tenant one-file-per-event backend
    DEFAULT_TENANT_ID                 -- the Phase 2 conventional default
    AgentIdentity, source_id_for      -- the multi-writer source_id contract
    FORBIDDEN_SOURCE_TOKEN_KINDS      -- names of tokens that MUST NOT be used
"""
from __future__ import annotations

from .agent_identity import (
    FORBIDDEN_SOURCE_TOKEN_KINDS,
    AgentIdentity,
    source_id_for,
)
from .events import (
    CandidateEvent,
    CandidatePayload,
    CandidateRiskPayload,
    CandidateUncertaintyPayload,
    DecayEvent,
    ObservationEvent,
    ObservedSignal,
)
from .file_store import (
    DEFAULT_TENANT_ID,
    RUNS_SUBDIR,
    EventStore,
    FileEventStore,
    RunsEventStore,
    new_run_id,
)

__all__ = [
    "DEFAULT_TENANT_ID",
    "FORBIDDEN_SOURCE_TOKEN_KINDS",
    "RUNS_SUBDIR",
    "AgentIdentity",
    "CandidateEvent",
    "CandidatePayload",
    "CandidateRiskPayload",
    "CandidateUncertaintyPayload",
    "DecayEvent",
    "EventStore",
    "FileEventStore",
    "RunsEventStore",
    "ObservationEvent",
    "ObservedSignal",
    "new_run_id",
    "source_id_for",
]
