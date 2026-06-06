"""The immutable observation-event model appended to the store.

An event is what ONE agent observed during ONE attempt at a goal. It carries raw
observations of signals — NOT a procedure, NOT selectors, NOT steps. The merge
projection folds many events into the believed `KnowledgeFile`; the store never
stores believed state, only raw events (ADR-0001).
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
