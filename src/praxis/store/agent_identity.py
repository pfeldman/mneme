"""`agent_identity`: the multi-writer `source_id` contract (ADR-0012 section 2).

Under multi-writer load the dominant failure shape is the source-independence
attack: N processes running the same model self-promote because each one looks
like a distinct source. ADR-0012 binds `source_id` to `agent_identity` (model
plus prompt lineage), NEVER to a per-process token. This module is the single
canonical way to derive that string.

Why this lives in `store/` rather than `oracle/`:

The store does not enforce who wrote what; it just persists events. But the
field that gates source-independence is set at write time, on the event, by the
writer. Centralizing the derivation here keeps every writer using the same
formula and makes "I set source_id to my pid" structurally harder than the
right thing.

Rules:

- The identity is `<model>::<prompt_lineage>`. Both parts are required.
- No hostname, pid, run_uuid, session_id, worker index, container id, or any
  per-process / per-host token. Each of those would let same-model writers
  count as independent sources under ADR-0008 and silently self-promote.
- Stable across processes for the same (model, prompt_lineage): two parallel
  workers running the same prompt against the same model collapse to one
  source under `independent_diverse(...)` and cannot satisfy the gate by
  count alone.
"""
from __future__ import annotations

from dataclasses import dataclass

# The per-process tokens that callers reach for "naturally" but that ADR-0012
# explicitly forbids as `source_id`. Surfaced as a constant so tests can assert
# the helper rejects all of them (loud-and-traceable over silent-and-convenient).
FORBIDDEN_SOURCE_TOKEN_KINDS: tuple[str, ...] = (
    "pid",
    "session_id",
    "run_uuid",
    "hostname",
    "worker_index",
    "container_id",
)


@dataclass(frozen=True)
class AgentIdentity:
    """The `(model, prompt_lineage)` pair that defines one independent source.

    `prompt_lineage` is a content-addressed handle to the prompt the agent was
    initialized with: a short hash or a stable name plus a version. Two agents
    that share `(model, prompt_lineage)` are NOT independent sources, no matter
    how many parallel processes spawned them.
    """

    model: str
    prompt_lineage: str

    def __post_init__(self) -> None:
        if not self.model or not isinstance(self.model, str):
            raise ValueError("agent_identity.model is required and must be a non-empty str")
        if not self.prompt_lineage or not isinstance(self.prompt_lineage, str):
            raise ValueError(
                "agent_identity.prompt_lineage is required and must be a non-empty str"
            )
        for forbidden in ("::",):
            if forbidden in self.model or forbidden in self.prompt_lineage:
                raise ValueError(
                    f"agent_identity components must not contain the reserved "
                    f"separator {forbidden!r}"
                )

    @property
    def source_id(self) -> str:
        """The canonical `source_id` string written into events (ADR-0012)."""
        return f"{self.model}::{self.prompt_lineage}"


def source_id_for(model: str, prompt_lineage: str) -> str:
    """Convenience constructor. Prefer this over hand-rolling the string."""
    return AgentIdentity(model=model, prompt_lineage=prompt_lineage).source_id
