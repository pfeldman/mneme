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
    # ADR-0033: a ref-tagged confirmation of an ENUMERATED seed signal. `ref` is
    # the stable prompt ref (S1..Sn for success seeds, F1..Fm for failure seeds,
    # positional within the run's KnowledgeFile snapshot); the runner binds the
    # confirmation to its seed by IDENTITY and SYSTEM-STAMPS the seed's declared
    # `type` and `value` onto this observation (the agent never restates seed
    # text). `evidence` is the MANDATORY concrete grounded detail (the agent's
    # own words: the literal text, status, route, count) for a `present: true`
    # confirmation; it is what the ADR-0030 predicate tier evaluates and what
    # the audit record preserves forever. `flags` are BODY-computed audit
    # markers (the ADR-0033 decision 5 advisory tripwires plus `void:*` reasons
    # for a malformed confirmation, decision 4); they never change a verdict.
    # All three default None so every pre-ADR-0033 envelope is unaffected.
    ref: str | None = None
    evidence: str | None = None
    flags: list[str] | None = None


class ObservationEvent(BaseModel):
    """An append-only record of one agent's observations for one goal attempt."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    ts: datetime = Field(default_factory=_now)
    schema_version: Literal["0"] = SCHEMA_VERSION
    agent_id: str
    goal_id: str
    observed_app_version: str | None = None
    # ADR-0035 decision 4: the deployment environment this observation was made
    # on. Operational provenance, like `agent_identity`: a partition key the
    # adapter filters projections by, NEVER a source dimension (decision 5) -
    # the model and store layers do not interpret it. Defaults None so every
    # pre-ADR-0035 event file (no key) stays valid; None also means "undeclared
    # project" (ADR-0001: additive event fields only).
    environment: str | None = None
    signals: list[ObservedSignal] = Field(default_factory=list)


class RegressObservationEvent(BaseModel):
    """A NON-PROMOTABLE, append-only record of what a regress run observed for
    one goal that reached a verdict (ADR-0023 decision 4 traceability).

    This is a sibling event kind, NOT an `ObservationEvent`. The distinction is
    the whole point: an `ObservationEvent` is the PROMOTABLE evidence the merge
    projection folds into belief, and ADR-0029 disabled the regress runner from
    persisting those because each confirmation run grew the believed success set
    (defect A: 4 seeded signals inflating to 26 agent-sourced ones). A
    `RegressObservationEvent` is read by NO projection and NO oracle gate; it
    lives in its own `regress/` store subdirectory so the believed-state
    projection's `*.json` glob over `events/` never sees it. It can never
    promote, so it does not reintroduce defect A.

    What it buys: a REGRESSED verdict is now traceable after the fact. Before
    this event existed, a failing console regress left only the aggregate
    markdown report; `local/events/` was empty, so there was no record of what
    the agent actually observed and no way to tell a real regression from a
    brain / observability miss. This event is that record - the brain's grounded
    observation envelope for the run, redacted at the adapter boundary, carrying
    the computed verdict so the audit trail is self-contained.

    It is NOT a procedure: it stores the same `ObservedSignal` envelope an
    `ObservationEvent` carries (signals only, no clicks, no selectors), plus the
    deterministic verdict the runner computed from them.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    ts: datetime = Field(default_factory=_now)
    schema_version: Literal["0"] = SCHEMA_VERSION
    kind: Literal["regress"] = "regress"
    agent_id: str
    goal_id: str
    # The deterministic per-goal verdict the runner computed in-memory from
    # `signals` (ADR-0009): "pass" / "fail" / "uncertain" / "auth_expired". It is
    # stored verbatim so the audit record is self-contained: replaying the log
    # tells you what the run concluded without re-running the matcher.
    verdict: str
    observed_app_version: str | None = None
    # ADR-0035 decision 4: the deployment environment this regress run checked.
    # Same posture as on `ObservationEvent`: operational provenance, additive,
    # defaults None so pre-ADR-0035 records (no key) stay valid.
    environment: str | None = None
    signals: list[ObservedSignal] = Field(default_factory=list)
    # ADR-0033 decision 4: the void confirmations of this run, named with their
    # reasons ("unknown ref 'S9'", "S1 present:true with empty evidence", ...).
    # A void that bound to a seed ALSO rides as a `void:*`-flagged signal above;
    # an unbindable void (unknown ref, malformed entry) has no seed to stamp, so
    # this list is the only place the record can name it. Additive, defaults
    # None so every pre-ADR-0033 record is unaffected (ADR-0001: additive event
    # fields only).
    voids: list[str] | None = None


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
    # ADR-0035 decision 4: the environment whose partitioned projection fired
    # this flip, so replaying one environment's log reconstructs that
    # environment's flips without touching another's. Additive, defaults None
    # so pre-ADR-0035 records (no key) stay valid.
    environment: str | None = None
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
    # ADR-0035 decisions 4 + 6: the environment the candidate was observed on.
    # Provenance only - review and the explore report annotate with it ("seen
    # on dev2 only"); it adds NO corroboration diversity (decision 5) and never
    # enters the payload. Defaults None so pre-ADR-0035 files stay valid.
    environment: str | None = None
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
