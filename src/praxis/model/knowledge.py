"""Pydantic models + YAML I/O for the Phase-1 knowledge schema.

The pydantic model is the typed mirror of `schema/knowledge.schema.json`. The JSON
Schema remains the single source of truth for *shape* (ADR-0002); a test asserts
the two agree (`tests/test_model_schema_agree.py`). Ordering of `SignalType` is
semantic - most-to-least durable - and the oracle relies on it (different types
are independent evidence; same-type repeats are not).

Phase 1 activates risks (with a structured `trigger`) and uncertainties as
first-class top-level arrays on `KnowledgeFile` (ADR-0009). `states` and `paths`
stay deferred (Phase 2). `risks.trigger` is a discriminated union (HTTP or
sequence form): free-text triggers are rejected at validation time to keep
schema rot bounded.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal["0"] = "0"


class SignalType(str, Enum):
    """Signal kinds, ordered most→least durable. Selectors/xpath/coordinates are
    deliberately NOT representable (invariants, not coordinates)."""

    BEHAVIORAL = "behavioral"
    NETWORK = "network"
    ACCESSIBILITY = "accessibility"
    TEXT = "text"
    URL = "url"
    VISUAL = "visual"


class SourceType(str, Enum):
    """Who authored an assertion. human/spec = seeded oracle, trusted from
    cold-start; agent = self-observed, needs evidence diversity (ADR-0005)."""

    HUMAN = "human"
    SPEC = "spec"
    AGENT = "agent"


class Status(str, Enum):
    """Believed-state of an assertion (computed by merge/oracle, never raw input
    in spirit, but stored so a hand-seeded file is explicit)."""

    BELIEVED = "believed"
    CONTESTED = "contested"
    STALE = "stale"
    QUARANTINED = "quarantined"


class _Base(BaseModel):
    # extra="forbid" mirrors `additionalProperties: false` in the JSON Schema.
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Provenance(_Base):
    """Where an assertion came from. Mandatory on every signal (ADR-0004).

    `observation_count` raises confidence WITHIN one signal; it does NOT create
    independence — two runs of the same model are not independent (ADR-0005).
    """

    source_type: SourceType
    source_id: str
    observed_app_version: str | None = None
    last_verified: datetime
    observation_count: int = Field(ge=1)


class Signal(_Base):
    """An observable oracle/recognition assertion. Carries provenance + confidence
    + status (all mandatory)."""

    type: SignalType
    value: str
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)
    status: Status


class Target(_Base):
    app: str
    environment: str | None = None
    observed_app_versions: list[str] | None = None


class Meta(_Base):
    created_at: datetime
    updated_at: datetime
    contributing_agents: list[str] | None = None


class HttpTrigger(_Base):
    """A risk trigger expressed as a concrete HTTP probe.

    Structured by design (ADR-0009 sec 4): a stranger reading this trigger can
    execute the probe deterministically, and the projection / E-mode prompt can
    render it without an LLM interpreting free text. The schema-rot vector
    closed by replacing free-text triggers like "under high load".
    """

    kind: Literal["http"] = "http"
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]
    path: str = Field(min_length=1)
    body_or_params: dict[str, Any] | None = None
    expect: str = Field(min_length=1)


class SequenceTrigger(_Base):
    """A risk trigger expressed as N repetitions of an action with a postcondition.

    Used for idempotency / race / replay regressions: "2x submit checkout
    returns 200 with same order_id". `action` is an intent ("submit
    checkout"), never a UI selector (AGENTS.md non-negotiable 1).
    """

    kind: Literal["sequence"] = "sequence"
    n: int = Field(ge=1)
    action: str = Field(min_length=1)
    expect: str = Field(min_length=1)


Trigger = Annotated[HttpTrigger | SequenceTrigger, Field(discriminator="kind")]


class Risk(_Base):
    """A hypothesized failure mode with an observable trigger (ADR-0009).

    Provenance + confidence + status are mandatory (ADR-0004): a seeded risk
    (source_type human/spec) is `believed` from cold-start; an agent-written
    risk enters as `contested` and needs source-independent corroboration to
    promote (ADR-0005, ADR-0008). E-mode reads believed + contested risks and
    probes their `trigger`; a matching observation produces a failure signal,
    not a status flip on the risk itself.
    """

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    trigger: Trigger
    mitigation: str | None = None
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)
    status: Status


class Uncertainty(_Base):
    """An open question an agent could not resolve (docs/03).

    First-class so that exploration is driven by gaps, not by re-running goals
    the agent already passed (the coverage-collapse risk in docs/05). An
    uncertainty becomes `resolved` when a corresponding observation answers
    it; resolution is recorded by setting `resolved=True` and pointing at the
    resolving signal value (cross-ref).
    """

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    raised_by: str = Field(min_length=1)
    raised_at: datetime
    resolved: bool = False
    resolving_signal_value: str | None = None


class KnowledgeFile(_Base):
    """A goal-scoped knowledge entry (one `*.knowledge.yaml`). Phase 1.

    Phase 1 activates `risks` and `uncertainties` as first-class arrays
    (ADR-0009). `states` and `paths` from the reference schema stay deferred
    to Phase 2: no experiment consumes them, naming them would invite schema
    rot (docs/06).
    """

    schema_version: Literal["0"]
    goal_id: str
    goal: str
    target: Target
    success_signals: list[Signal] = Field(min_length=1)
    failure_signals: list[Signal] | None = None
    risks: list[Risk] | None = None
    uncertainties: list[Uncertainty] | None = None
    meta: Meta


# --------------------------------------------------------------------------- I/O


def to_jsonable(model: KnowledgeFile) -> dict[str, Any]:
    """Plain JSON/YAML-friendly dict: enums→str, datetimes→ISO-8601, no Nones.

    Dropping `None` keeps optional fields absent rather than emitting `null`
    (which the schema's typed properties would reject)."""
    return model.model_dump(mode="json", exclude_none=True)


def dumps(model: KnowledgeFile) -> str:
    """Serialize a KnowledgeFile to YAML text (key order preserved)."""
    return yaml.safe_dump(to_jsonable(model), sort_keys=False, allow_unicode=True)


def dump(model: KnowledgeFile, path: str | Path) -> None:
    """Write a KnowledgeFile to a `*.knowledge.yaml` file."""
    Path(path).write_text(dumps(model), encoding="utf-8")


def loads(text: str) -> KnowledgeFile:
    """Parse + validate a KnowledgeFile from YAML text."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("knowledge YAML must be a mapping at the top level")
    return KnowledgeFile.model_validate(data)


def load(path: str | Path) -> KnowledgeFile:
    """Read + validate a KnowledgeFile from a `*.knowledge.yaml` file."""
    return loads(Path(path).read_text(encoding="utf-8"))


# Resolve the bundled JSON Schema relative to the repo, regardless of CWD.
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schema" / "knowledge.schema.json"


def validate_against_json_schema(data: dict[str, Any], *, schema_path: Path | None = None) -> None:
    """Validate a plain dict against `schema/knowledge.schema.json`.

    `jsonschema` is a dev-only dependency, so it is imported lazily here — the
    core (`import praxis.model`) stays on pydantic + pyyaml only (ADR-0003).
    Raises `jsonschema.ValidationError` on failure.
    """
    import json

    import jsonschema  # local import: dev-only dep, keep core runtime-agnostic

    path = schema_path or _SCHEMA_PATH
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)
