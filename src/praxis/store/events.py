"""The immutable observation-event model appended to the store.

An event is what ONE agent observed during ONE attempt at a goal. It carries raw
observations of signals - NOT a procedure, NOT selectors, NOT steps. The merge
projection folds many events into the believed `KnowledgeFile`; the store never
stores believed state, only raw events (ADR-0001).

Phase 2 introduces TWO sibling event kinds:

- `DecayEvent` (ADR-0013): records a *status flip* caused by recency decay. It is
  written by the projection driver when re-evaluating `independent_diverse(...)`
  over the surviving set demotes a signal to `stale`. The store stays
  append-only; decay events live side by side with observation events. Numeric
  confidence drift between projections is NOT an event - only status flips emit
  one.

- `CandidateEvent` (ADR-0014): an agent-proposed risk or uncertainty (NOT a
  signal observation). It has its own `schema_version` and is read/written
  through dedicated store paths so the diversity gate over signal observations
  cannot accidentally count candidate writes as evidence (ADR-0008
  source-independence hardening).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..model import (
    Provenance,
    Risk,
    SignalType,
    SourceType,
    Uncertainty,
)

SCHEMA_VERSION: Literal["0"] = "0"
CANDIDATE_SCHEMA_VERSION: Literal["0"] = "0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class ObservedSignal(BaseModel):
    """One raw signal observation inside an event.

    `present=False` records an explicit NEGATIVE observation ("I did NOT see the
    logout action"); merge uses positives vs negatives to detect contradiction
    and oscillation, rather than silently dropping disagreement. `confidence` is
    optional and only meaningful for seeded (human/spec) signals, where it acts
    as a confidence floor; for agent observations merge computes confidence.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["success", "failure"]
    type: SignalType
    value: str
    present: bool = True
    source_type: SourceType
    source_id: str
    observed_app_version: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # The structured payload for a signal whose target carries an ADR-0031
    # `check`: the agent self-reports the raw data the check evaluates (a
    # `list_count_delta` -> {before_count, after_count}; an `element_membership`
    # -> {identifier, present}). Optional and defaults None so every free-text /
    # value_predicate observation is unaffected. The matcher passes it to
    # `evaluate_check`, which FAILS CLOSED on a missing or malformed payload
    # (ADR-0031 decision 5); it is per-run observation data redacted at the
    # adapter boundary, never durable knowledge (ADR-0031 forbidden alt).
    observed: dict[str, Any] | None = None


class ObservationEvent(BaseModel):
    """An append-only record of one agent's observations for one goal attempt."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    ts: datetime = Field(default_factory=_now)
    schema_version: Literal["0"] = SCHEMA_VERSION
    agent_id: str
    goal_id: str
    observed_app_version: str | None = None
    signals: list[ObservedSignal] = Field(default_factory=list)


class DecayEvent(BaseModel):
    """An immutable, append-only record that a recency-decay re-evaluation
    flipped a projected signal's status (ADR-0013).

    Decay is a projection-time derivation; confidence drift is pure (no event
    is written). A `DecayEvent` is written ONLY when the status of a projected
    signal changes because its supporting evidence aged out under the
    pre-registered anchor (observed_app_version primary, wall-clock secondary).

    The event identifies:
      - the signal that flipped (via its grouping key, since signals are not
        first-class store entities),
      - the prior status and the new status,
      - the retired event ids whose age triggered the flip,
      - the projection anchor used (`current_version` plus the wall-clock
        timestamp), so replaying the log reconstructs the same status without
        re-reading the clock.

    Re-promotion from `stale` to `believed` is NOT a decay event - it goes
    through the ADR-0008 cold-start gate and is decided by the projection
    over fresh `ObservationEvent`s. Decay is unidirectional.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    ts: datetime = Field(default_factory=_now)
    schema_version: Literal["0"] = SCHEMA_VERSION
    kind: Literal["decay"] = "decay"
    goal_id: str
    # The grouping key of the projected signal that flipped. Mirrors the
    # `(signal_kind, type, value)` tuple the projection groups observations by.
    signal_kind: Literal["success", "failure"]
    signal_type: SignalType
    signal_value: str
    from_status: Literal["believed", "contested"]
    to_status: Literal["stale"]
    retired_event_ids: list[str] = Field(default_factory=list)
    anchor_current_version: str | None = None
    # The wall-clock used by the projection that fired the flip. Replaying the
    # log uses this, not the replayer's clock.
    anchor_now: datetime
    # The rule that fired: "version" (>= N minors behind current_version),
    # "wallclock" (>= T days old), or "both".
    rule: Literal["version", "wallclock", "both"]
    # Free-form audit note: who fired (the projection driver), thresholds used.
    note: str | None = None


# --- Phase 2 (ADR-0014): candidate events ---------------------------------


class CandidateRiskPayload(BaseModel):
    """An agent-proposed Risk. Mirrors the seeded `Risk` shape.

    The structured `trigger` is mandatory and the same validator
    (`model.trigger_validator.validate_risk`) that gates seeded risks gates
    this payload at the adapter boundary (ADR-0014 sec 3). The agent author
    is `provenance.source_id`, which the runner sets to `agent_identity`
    (NOT `run_uuid`) so N same-model runs count as ONE source under the
    independence rule (ADR-0008 + ADR-0014 sec 2).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["candidate_risk"] = "candidate_risk"
    risk: Risk


class CandidateUncertaintyPayload(BaseModel):
    """An agent-proposed Uncertainty. Mirrors the seeded `Uncertainty` shape.

    `author` is `raised_by` on the underlying Uncertainty; `timestamp` is
    `raised_at`. Both are mandatory (ADR-0014 sec 3); the validator is
    structural here - uncertainties are questions, not predicates, so there
    is no banned-phrase set to scan.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["candidate_uncertainty"] = "candidate_uncertainty"
    uncertainty: Uncertainty


CandidatePayload = Annotated[
    CandidateRiskPayload | CandidateUncertaintyPayload,
    Field(discriminator="kind"),
]


class CandidateEvent(BaseModel):
    """An agent-proposed candidate risk or uncertainty (ADR-0014).

    Sibling to `ObservationEvent`, NOT an extension: bundling agent-proposed
    risks into the signal envelope would let the oracle gate count candidate
    writes as evidence (ADR-0008 schema-drift vector). `schema_version` is
    independent of `ObservationEvent.schema_version` so the candidate payload
    can evolve without invalidating prior signal events under the append-only
    contract (ADR-0001).

    `agent_identity` is the source_id under the independence rule (ADR-0014
    sec 2 + ADR-0008). It MUST match `payload.risk.provenance.source_id`
    (for risks) or `payload.uncertainty.raised_by` (for uncertainties);
    the adapter enforces this at write time.

    `observed_app_version` is the version the candidate was authored under,
    used by the projection's decay rule (ADR-0013).
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    ts: datetime = Field(default_factory=_now)
    schema_version: Literal["0"] = CANDIDATE_SCHEMA_VERSION
    agent_identity: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    observed_app_version: str | None = None
    payload: CandidatePayload

    @property
    def candidate_id(self) -> str:
        """The id of the underlying Risk or Uncertainty.

        Stable across re-writes by the same agent_identity; the projection
        groups corroborations under this id when checking promotion.
        """
        if isinstance(self.payload, CandidateRiskPayload):
            return self.payload.risk.id
        return self.payload.uncertainty.id

    @property
    def candidate_kind(self) -> str:
        return self.payload.kind

    @property
    def provenance(self) -> Provenance | None:
        """The provenance attached to the underlying Risk; None for uncertainties.

        Uncertainties carry only `raised_by` + `raised_at` (questions, not
        assertions), so there is no Provenance to surface (ADR-0004 distinction).
        """
        if isinstance(self.payload, CandidateRiskPayload):
            return self.payload.risk.provenance
        return None
