"""The immutable observation-event model appended to the store.

An event is what ONE agent observed during ONE attempt at a goal. It carries raw
observations of signals — NOT a procedure, NOT selectors, NOT steps. The merge
projection folds many events into the believed `KnowledgeFile`; the store never
stores believed state, only raw events (ADR-0001).

Phase 2 introduces a sibling event kind: `DecayEvent` (ADR-0013). A decay event
records a *status flip* caused by recency decay: it is written by the projection
driver when re-evaluating `independent_diverse(...)` over the surviving set
demotes a signal to `stale`. The store stays append-only; decay events live
side by side with observation events. Numeric confidence drift between
projections is NOT an event - only status flips emit one.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..model import SignalType, SourceType

SCHEMA_VERSION: Literal["0"] = "0"


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
