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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    + status (all mandatory).

    `value_predicate` (ADR-0030) is the OPTIONAL checkable form of `value`: a
    template string whose text outside a `{slot}` is an INVARIANT matched
    exactly and whose declared slots are per-run instance tokens the matcher
    tolerates on presence/shape only. It SUPPLEMENTS `value` (which stays
    required and is the projection grouping key), never replaces it. A free-text
    signal leaves it None and is matched exactly as before (Jaccard over the
    prose `value`, ADR-0028). When present, the matcher evaluates the predicate
    instead of Jaccard (decision 2)."""

    type: SignalType
    value: str
    value_predicate: str | None = Field(default=None, min_length=1)
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)
    status: Status

    @field_validator("value_predicate")
    @classmethod
    def _value_predicate_is_valid(cls, v: str | None) -> str | None:
        """Validate a declared `value_predicate` at the write boundary (ADR-0030
        decision 6), the same posture as `trigger_validator.validate_trigger`.

        A malformed predicate (no invariant, malformed slot, unknown shape,
        stopword-only invariant) is a LOUD pydantic rejection here, never a
        silent downgrade to the free-text path. A free-text signal leaves the
        field None and skips the check. The parser is the single source of the
        validation rules (it is also what the matcher evaluates), so the write
        boundary and the matcher can never drift on what a valid predicate is.
        """
        if v is None:
            return v
        # Local import keeps the cycle direction one-way (predicate imports
        # nothing from this module) and the parse is pure / stdlib (ADR-0003).
        from .predicate import PredicateError, parse

        try:
            parse(v)
        except PredicateError as exc:
            raise ValueError(
                f"value_predicate rejected (ADR-0030 decision 6): {exc}"
            ) from exc
        return v


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


# --- Phase-2 projected field: auth_state (ADR-0017) --------------------------

# Forbidden substrings on `scope` and on observation values feeding auth_state.
# Per ADR-0017 sec 2, auth_state MUST NOT carry: access/refresh tokens, API
# keys, bearer strings, cookies, user/account/session identifiers, emails, JWT
# contents, or any per-session/per-user data. The validator rejects loudly so
# that a wrong write becomes a traceable event at the boundary, not a silent
# leak (AGENTS.md non-negotiable + docs/06 leakage).
_AUTH_STATE_FORBIDDEN_SCOPE_TOKENS: tuple[str, ...] = (
    "token",
    "bearer",
    "cookie",
    "session_id",
    "sid",
    "user_id",
    "account_id",
    "uid",
    "jwt",
    "@",  # any email-shaped scope
    "api_key",
    "apikey",
    "tenant_id",  # ADR-0017 sec 5 forbidden alternatives
    "org_id",
    "workspace_id",
)

# Allowed canonical scope strings; an SUT may register additional role strings
# in its knowledge file, but per-session identifiers are always rejected.
_AUTH_STATE_RECOMMENDED_SCOPES: frozenset[str] = frozenset(
    {"anonymous", "user", "admin"}
)


class AuthState(_Base):
    """Projected authentication posture for a goal (ADR-0017).

    Two subfields only:
      * `authenticated` - whether the projection believes the current session
        is authenticated, derived from observable behavioral or network
        signals via the same diversity-or-seed rule as any other oracle
        (ADR-0008). This is NOT a new oracle; promotion reuses
        `oracle/trust.py` over the underlying signals.
      * `scope` - abstract role the projection believes the session occupies
        (`anonymous`, `user`, `admin`, or a SUT-specific role string the
        knowledge file registers). `null` when `authenticated` is false or
        when surviving evidence is too thin to claim a scope.

    What `auth_state` MUST NOT carry (rejected at write time):

    * access/refresh tokens, API keys, bearer strings;
    * cookies (raw or parsed) and cookie names that double as session IDs;
    * user identifiers (`user_id`, `account_id`, email, username);
    * session identifiers (`session_id`, `sid`, JWT contents);
    * any per-session or per-user generated value;
    * tenant/org/workspace scoping (ADR-0012 owns tenant paths).

    The adapter is the redaction point; the schema/model is the contract that
    says what may cross. Rejection here is loud (pydantic ValidationError),
    not silent.
    """

    authenticated: bool
    # `scope` is REQUIRED on write but its value is nullable: a writer must
    # decide whether the projection believes a scope or explicitly null
    # (ADR-0017 sec 1: null when authenticated is false or surviving evidence
    # is too thin). Pydantic `... = Field(...)` mirrors the schema's required
    # list; the field still accepts `None`.
    scope: str | None = Field(...)
    # `being_tested` declares whether authentication is the SUBJECT under test
    # (the login flow itself is what the goal verifies) or merely a PRECONDITION
    # (ADR-0027 decision 1). Optional, defaults False (precondition), so existing
    # knowledge files with no `being_tested` key still validate. When True, a run
    # performs a real login and does NOT reuse a saved session (ADR-0027
    # decision 2), because reusing a session would skip the flow under test.
    being_tested: bool = False

    @field_validator("scope")
    @classmethod
    def _scope_must_not_carry_secrets(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.strip():
            raise ValueError(
                "auth_state.scope must be a non-empty role string or null; "
                "got an empty/whitespace value."
            )
        lowered = v.lower()
        for forbidden in _AUTH_STATE_FORBIDDEN_SCOPE_TOKENS:
            if forbidden in lowered:
                raise ValueError(
                    f"auth_state.scope rejected: contains forbidden token "
                    f"{forbidden!r} (ADR-0017 sec 2). scope must be an "
                    f"abstract role like 'anonymous', 'user', 'admin', or a "
                    f"SUT-specific role string; tokens, cookies, user/session "
                    f"ids, and PII are never durable knowledge."
                )
        # An obvious JWT shape (three dot-separated base64-url chunks) is
        # rejected even if it slipped past the token-name filter.
        parts = v.split(".")
        if len(parts) == 3 and all(
            len(p) >= 8 and all(c.isalnum() or c in "-_" for c in p) for p in parts
        ):
            raise ValueError(
                "auth_state.scope rejected: value looks like a JWT (three "
                "base64url segments separated by dots). scope is an abstract "
                "role, never a credential (ADR-0017 sec 2)."
            )
        return v

    @model_validator(mode="after")
    def _scope_null_when_unauthenticated(self) -> "AuthState":
        if not self.authenticated and self.scope is not None:
            raise ValueError(
                "auth_state.scope must be null when authenticated is false "
                "(ADR-0017 sec 1: an unauthenticated session has no scope)."
            )
        return self

    @classmethod
    def recommended_scopes(cls) -> frozenset[str]:
        """Canonical scope strings. SUTs MAY register additional roles by
        using them in seeded knowledge; the validator only rejects forbidden
        tokens, not unseen role strings."""
        return _AUTH_STATE_RECOMMENDED_SCOPES


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
    auth_state: AuthState | None = None
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
