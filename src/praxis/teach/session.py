"""The teach session seams: the LIBRARY half of `/praxis:teach` (ADR-0022).

This module is the non-interactive machinery the `/praxis:teach` Claude Code
skill (ADR-0022 decision 1, authored in Step 10) drives. It is deliberately
brain-free and browser-free: a teach session is always human-in-the-loop and
drives a live app through the ADR-0003 Playwright adapter, but NONE of that
reasoning or driving lives here. What lives here is the testable contract the
skill must obey:

- The typed prompt protocol (ADR-0022 decision 2): when the brain is blocked it
  asks the human EXACTLY ONE of four declared question types - credential,
  navigation-hint, role, confirmation. Modeled here as data so the skill and the
  tests speak the same shapes and the credential type can be routed to the
  never-persist path.
- A navigation-hint reply is recorded as the behavioral / network /
  accessibility / text / url INVARIANT it points at, in that preference order,
  NEVER as a raw CSS selector, XPath, or coordinate (AGENTS.md non-negotiable 1,
  ADR-0022 decision 2). `record_navigation_hint` rejects a selector-shaped reply.
- The dual end condition (ADR-0022 decision 3): a session ends successfully only
  when BOTH the happy path was observed (a believed-grade success signal) AND
  the human answered a confirmation prompt. A per-session action / token budget
  plus a wall-clock backstop bounds a session that never converges; on
  exhaustion the session writes NO goal and emits a LOUD, traceable
  not-converged event naming what was reached and what was missing.
- The human-seeded output (ADR-0022 decision 4): the artifact of a confirmed
  session is a goal YAML whose success oracle carries `provenance.source_type =
  human` (the confirming human), the legitimate ADR-0005 first-oracle seed path.
  Provenance + confidence are mandatory on every emitted signal and risk; author
  + timestamp on every uncertainty (ADR-0004). The emitted knowledge is
  OPERATIONAL (signals, risks with structured triggers, uncertainties), never a
  click-by-click procedure (AGENTS.md "knowledge not a procedure cache").
- The credential contract (ADR-0022 decision 5, ADR-0017): a credential typed
  during teach (or read from the ADR-0021 secrets channel) drives the browser
  for this session only and is NEVER persisted to any file under `.praxis/`, any
  log, or any emitted assertion. Only the abstract `auth_state`
  (`authenticated` + `scope`) is recorded; the adapter-boundary validator
  rejects tokens, cookies, user/session IDs, JWT contents, and PII.
- No silent overwrite of a believed goal (ADR-0022 decision 6): if the named
  goal already exists believed in `.praxis/knowledge/`, the session does NOT
  edit it in place; it appends a contested candidate refinement under
  `.praxis/candidates/` (an ADR-0014 `CandidateEvent`, via the Wave 1
  `CandidateFileStore` writer). Promotion stays a human seed via git merge
  (ADR-0018, ADR-0001), never an in-place mutation.

The module imports only model / store / secrets, so importing it pulls no
runtime and no brain (ADR-0003, ADR-0019); it performs no I/O at import time.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..model import (
    AuthState,
    KnowledgeFile,
    Meta,
    Provenance,
    Risk,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
    Trigger,
    Uncertainty,
    dump,
)
from ..model.trigger_validator import validate_trigger
from ..store import (
    CandidateEvent,
    CandidateFileStore,
    CandidateRiskPayload,
    CandidateUncertaintyPayload,
)

__all__ = [
    "PromptType",
    "TeachPrompt",
    "CredentialPrompt",
    "NavigationHintPrompt",
    "RolePrompt",
    "ConfirmationPrompt",
    "NavigationInvariant",
    "INVARIANT_PREFERENCE_ORDER",
    "SelectorLikeReply",
    "record_navigation_hint",
    "CredentialLeak",
    "assert_no_credential_leak",
    "TeachBudget",
    "EndCondition",
    "NotConvergedEvent",
    "TeachOutcome",
    "TeachSession",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- the typed prompt protocol (ADR-0022 decision 2) ----------------------


class PromptType(str, Enum):
    """The four declared teach question types (ADR-0022 decision 2).

    When the brain is blocked it asks the human a question of EXACTLY ONE of
    these types, never an open-ended free-text dump. Naming the type keeps the
    protocol machine-checkable and lets the credential type be routed to the
    never-persist path of decision 5.
    """

    CREDENTIAL = "credential"
    NAVIGATION_HINT = "navigation_hint"
    ROLE = "role"
    CONFIRMATION = "confirmation"


@dataclass(frozen=True)
class TeachPrompt:
    """Base of the typed prompt protocol: a question with a declared `type`.

    A concrete prompt is one of the four subclasses below; `prompt_type` pins
    the kind so a consumer (the skill, a test) can dispatch without guessing.
    The `question` is what the human reads. No subclass carries a CSS selector,
    a coordinate, or a credential value: the credential VALUE is the human's
    reply, never a field of the prompt (decision 5).
    """

    prompt_type: PromptType
    question: str


@dataclass(frozen=True)
class CredentialPrompt(TeachPrompt):
    """The brain needs a secret to pass an auth wall (decision 2).

    `key` names the credential the ADR-0021 secrets channel would supply (so the
    skill can offer the ask-or-fail append command); it is a KEY NAME, never a
    value. The reply (the secret) drives the browser for this session only and
    is governed by decision 5; it is never stored on this object.
    """

    key: str = ""

    @classmethod
    def for_key(cls, key: str, *, question: str | None = None) -> "CredentialPrompt":
        return cls(
            prompt_type=PromptType.CREDENTIAL,
            question=(
                question
                or f"I need the credential {key!r} to get past the auth wall."
            ),
            key=key,
        )


@dataclass(frozen=True)
class NavigationHintPrompt(TeachPrompt):
    """The brain cannot find the affordance that advances the happy path and
    asks where it is IN APP TERMS (decision 2): "which control opens the
    editor", "is there a confirmation step". The reply is a behavioral or text
    hint; `record_navigation_hint` rejects a selector-shaped reply."""

    @classmethod
    def asking(cls, question: str) -> "NavigationHintPrompt":
        return cls(prompt_type=PromptType.NAVIGATION_HINT, question=question)


@dataclass(frozen=True)
class RolePrompt(TeachPrompt):
    """The brain needs the abstract scope the goal targets (`anonymous`,
    `user`, `admin`, or a SUT-specific role string) so the emitted
    `auth_state.scope` is correct under ADR-0017 (decision 2)."""

    @classmethod
    def asking(
        cls, question: str = "which abstract role does this goal target?",
    ) -> "RolePrompt":
        return cls(prompt_type=PromptType.ROLE, question=question)


@dataclass(frozen=True)
class ConfirmationPrompt(TeachPrompt):
    """The brain believes it observed the happy path and asks the human to
    confirm the reached state is the intended success (decision 2). The
    affirmative reply is the human SEED act of decision 4: it is what makes the
    emitted oracle a legitimate ADR-0005 human seed."""

    @classmethod
    def asking(cls, question: str) -> "ConfirmationPrompt":
        return cls(prompt_type=PromptType.CONFIRMATION, question=question)


# --- navigation hints are invariants, never selectors (decision 2) --------

# The five durable invariant kinds a navigation-hint reply may be recorded as,
# in the ADR five-non-negotiables preference order (behavioral first, url last).
# `visual` is intentionally excluded: a navigation hint that can only be
# expressed as "the blue button top-right" is a coordinate in disguise and is
# refused, not downgraded to a visual signal.
INVARIANT_PREFERENCE_ORDER: tuple[SignalType, ...] = (
    SignalType.BEHAVIORAL,
    SignalType.NETWORK,
    SignalType.ACCESSIBILITY,
    SignalType.TEXT,
    SignalType.URL,
)

# Tokens that mark a reply as a raw CSS selector / XPath / coordinate rather
# than the behavior it points at. Kept small and concrete (the same posture as
# the trigger banned-phrase set); a selector-shaped reply is rejected loudly so
# a coordinate never becomes durable knowledge (AGENTS.md non-negotiable 1).
_SELECTOR_LIKE_TOKENS: tuple[str, ...] = (
    "css=",
    "xpath=",
    "//",  # XPath
    "::",  # pseudo-element / shadow piercing
    "queryselector",  # matched against the lowercased reply
    "getelementby",
    "data-testid",
    "data-test=",
    "#",  # id selector
    ".btn",  # class selector shapes
    ".class",
)


class SelectorLikeReply(ValueError):
    """A navigation-hint reply named a raw selector / coordinate, not a behavior.

    Raised by `record_navigation_hint` so a selector never becomes durable
    knowledge. The message names the offending reply so the skill can re-ask the
    human for the behavior the control performs, not where it is in the DOM.
    """


@dataclass(frozen=True)
class NavigationInvariant:
    """A navigation-hint reply recorded as the invariant it points at.

    `type` is one of `INVARIANT_PREFERENCE_ORDER` (behavioral .. url), never a
    selector. `value` is the behavior / network fact / role / text / url the
    hint pointed at, phrased as an observable invariant. This is what a
    navigation hint becomes in the emitted knowledge: a success or recognition
    signal value, never a click target.
    """

    type: SignalType
    value: str


def _looks_like_selector(reply: str) -> bool:
    lowered = reply.lower()
    for token in _SELECTOR_LIKE_TOKENS:
        if token in lowered:
            return True
    # A leading `#` or `.` followed by an identifier is the classic id / class
    # selector shape even without one of the explicit tokens above.
    stripped = reply.strip()
    if stripped[:1] in {"#", "."} and len(stripped) > 1 and (
        stripped[1].isalnum() or stripped[1] in "-_"
    ):
        return True
    return False


def record_navigation_hint(
    reply: str,
    *,
    invariant_type: SignalType = SignalType.BEHAVIORAL,
) -> NavigationInvariant:
    """Record a navigation-hint reply as the invariant it points at (decision 2).

    The reply must describe the BEHAVIOR the control performs ("the editor opens
    in a modal", "a /publish request fires and returns 2xx"), recorded as a
    behavioral / network / accessibility / text / url invariant. A reply that
    names a raw CSS selector, XPath, or coordinate raises `SelectorLikeReply`:
    a selector is never durable knowledge (AGENTS.md non-negotiable 1, ADR-0022
    decision 2). `invariant_type` defaults to the most durable kind (behavioral)
    and must be one of `INVARIANT_PREFERENCE_ORDER`; `visual` and any
    non-invariant type are refused so a coordinate cannot sneak in as a "visual"
    signal.
    """
    if not isinstance(reply, str) or not reply.strip():
        raise SelectorLikeReply(
            "navigation-hint reply is empty; ask the human what the control "
            "DOES (the behavior), not where it is in the DOM."
        )
    if invariant_type not in INVARIANT_PREFERENCE_ORDER:
        raise ValueError(
            f"navigation hints record only durable invariants "
            f"{[t.value for t in INVARIANT_PREFERENCE_ORDER]}; got "
            f"{invariant_type.value!r}. A hint is the behavior the control "
            f"performs, never a visual / coordinate target (ADR-0022 decision 2)."
        )
    if _looks_like_selector(reply):
        raise SelectorLikeReply(
            f"navigation-hint reply {reply!r} looks like a raw selector / "
            f"coordinate. Record the BEHAVIOR it points at (what the control "
            f"does), never the selector (AGENTS.md non-negotiable 1)."
        )
    return NavigationInvariant(type=invariant_type, value=reply.strip())


# --- the credential never crosses the persistence boundary (decision 5) ----

# Credential leakage is about VALUES, not descriptive nouns. The seed example
# files legitimately say "sets a session cookie" and ask "is the session cookie
# scoped per tab?" as operational knowledge (ADR-0017 records the abstract
# posture, the words describing it are fine). What a teach emit must reject is a
# concrete secret VALUE crossing into an assertion: a `key=value` / `key: value`
# assignment for a credential key, a `Bearer <token>` literal, a JWT, an email,
# or a long opaque secret-looking string. The check below targets those shapes,
# matching the AuthState scope validator's intent (reject the credential, allow
# the abstract role / description) extended to free-text emitted values.

# Credential KEY names that, when followed by an assignment, mark a real value.
# Matched only in `<key><sep><value>` form (the value-bearing shape), never as a
# bare descriptive noun.
_CREDENTIAL_ASSIGNMENT_KEYS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "session_id",
    "sessionid",
    "jsessionid",
    "sid",
    "api_key",
    "apikey",
    "access_key",
    "access_token",
    "refresh_token",
    "user_id",
    "account_id",
    "uid",
    "auth",
    "authorization",
    "cookie",
)

# `<key><optional ws>(=|:)<optional ws><non-space value>`: a credential key
# carrying an actual value. `set-cookie: x=y`, `session_id=abc123`,
# `password: hunter2` all trip; "the session cookie" / "is the cookie scoped"
# (no assignment) do not.
_ASSIGNMENT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _CREDENTIAL_ASSIGNMENT_KEYS) + r")\b"
    r"\s*[:=]\s*\S",
    re.IGNORECASE,
)

# `Bearer <token>` / `Authorization: Bearer <token>`: an auth header literal.
_BEARER_RE = re.compile(r"\bbearer\s+\S", re.IGNORECASE)

# An email address (PII): a concrete user identifier, never durable knowledge.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# A long opaque alphanumeric run (>= 20 chars, mixed case or with digits) that
# looks like a raw token / key / session id rather than prose. Prose words are
# short and lowercase; a 20+ char base64-ish blob is a value, not a sentence.
_OPAQUE_SECRET_RE = re.compile(r"\b[A-Za-z0-9_\-]{20,}\b")

# A long run of digits (>= 8) is a generated numeric identifier (a user_id, an
# account_id, an order id), never per-app-version durable knowledge (ADR-0017
# sec 2 forbids per-user generated values). Short numbers (HTTP 200, "3 failed
# logins", an `n=4` sequence) are operational and pass.
_LONG_DIGIT_RUN_RE = re.compile(r"\b\d{8,}\b")


class CredentialLeak(ValueError):
    """An emitted assertion carried a credential / token / cookie / id / PII.

    Raised by `assert_no_credential_leak` at the emit boundary so a secret never
    crosses into a committed file under `.praxis/`, a log, or an emitted signal
    (ADR-0022 decision 5). The message names the offending FIELD and the SHAPE
    that tripped the check, NEVER the surrounding value, so the exception itself
    does not leak the secret.
    """


def _looks_opaque_secret(value: str) -> bool:
    """True if `value` contains a long opaque alphanumeric run that is a value,
    not prose. A run of >= 20 token-chars that mixes case or carries digits is a
    raw token / key / session id; a normal English word never is."""
    for m in _OPAQUE_SECRET_RE.finditer(value):
        run = m.group(0)
        has_digit = any(c.isdigit() for c in run)
        has_upper = any(c.isupper() for c in run)
        has_lower = any(c.islower() for c in run)
        # Mixed-case or digit-bearing long run = a value (a token / hash / id).
        if has_digit or (has_upper and has_lower):
            return True
    return False


def _scan_value(field_name: str, value: str) -> None:
    """Reject a concrete secret VALUE in an emitted free-text field (decision 5).

    Targets value-shapes (assignments, Bearer literals, JWTs, emails, opaque
    secret runs), NOT descriptive nouns: "sets a session cookie" passes,
    "session_id=abc123" does not. The exception names the SHAPE, never the value.
    """
    def _raise(shape: str) -> None:
        raise CredentialLeak(
            f"{field_name} rejected: contains a {shape}. A teach session records "
            f"only the ADR-0017 abstract auth_state posture; credentials, tokens, "
            f"cookies, ids, and PII never cross into emitted knowledge (ADR-0022 "
            f"decision 5). Describe the behavior, not the secret value."
        )

    if _ASSIGNMENT_RE.search(value):
        _raise("credential key=value assignment")
    if _BEARER_RE.search(value):
        _raise("Bearer auth-header literal")
    if _EMAIL_RE.search(value):
        _raise("PII email address")
    # A JWT shape (three base64url segments) is rejected even without a key name.
    parts = value.split(".")
    if len(parts) == 3 and all(
        len(p) >= 8 and all(c.isalnum() or c in "-_" for c in p) for p in parts
    ):
        _raise("JWT-shaped value (three base64url segments)")
    if _LONG_DIGIT_RUN_RE.search(value):
        _raise("long numeric identifier (user / account / session id)")
    if _looks_opaque_secret(value):
        _raise("long opaque token / key / id value")


def _scan_trigger(field_prefix: str, trigger: Any) -> None:
    """Scan EVERY free-text / structured surface of a risk trigger (decision 5).

    A risk trigger is one of `HttpTrigger` (with a free-text `expect` AND a
    `body_or_params` dict) or `SequenceTrigger` (with a free-text `action` AND a
    free-text `expect`). The real password for the canonical teach goal ("a user
    can log in") lands in the POST /login `body_or_params` of the trace, so that
    dict is exactly where a credential VALUE crosses into committed knowledge if
    it is not scanned. This scans all three surfaces:

    - `expect` (HttpTrigger and SequenceTrigger);
    - `action` (SequenceTrigger);
    - `body_or_params` (HttpTrigger): each value is stringified through the same
      `_scan_value` shape check, and any credential-named KEY is rejected outright
      so a `{"password": "..."}` body cannot ride into a committed file.
    """
    expect = getattr(trigger, "expect", None)
    if expect:
        _scan_value(f"{field_prefix}.trigger.expect", expect)
    action = getattr(trigger, "action", None)
    if action:
        _scan_value(f"{field_prefix}.trigger.action", action)
    body = getattr(trigger, "body_or_params", None)
    if body:
        for key, val in body.items():
            # A credential-named KEY in the body marks a value-bearing secret even
            # if the value itself is short and shape-clean (a 6-char password).
            if str(key).lower() in _CREDENTIAL_ASSIGNMENT_KEYS:
                raise CredentialLeak(
                    f"{field_prefix}.trigger.body_or_params rejected: contains a "
                    f"credential key {str(key)!r}. A teach session records only the "
                    f"ADR-0017 abstract auth_state posture; credentials, tokens, "
                    f"cookies, ids, and PII never cross into emitted knowledge "
                    f"(ADR-0022 decision 5). Describe the behavior, not the secret."
                )
            # The value is also scanned for token / cookie / id / PII shapes; the
            # whole dict is canonicalized so a nested secret is not missed.
            _scan_value(f"{field_prefix}.trigger.body_or_params", json.dumps(val))
        _scan_value(
            f"{field_prefix}.trigger.body_or_params",
            json.dumps(body, sort_keys=True),
        )


def _scan_risk(field_prefix: str, risk: Risk) -> None:
    """Scan EVERY free-text / structured surface of a risk (decision 5).

    The single per-risk scanner both the seed path (`assert_no_credential_leak`)
    and the re-teach contested-refinement path (`emit_contested_refinement`) call,
    so a secret rejected on a new-goal seed is rejected identically on a re-teach.
    Covers `description`, `mitigation`, and the structured trigger's `expect`,
    `action`, and `body_or_params` (via `_scan_trigger`). Factoring this is the
    fix for the asymmetric-defense hole: the two paths can no longer drift to a
    narrower scan.
    """
    _scan_value(f"{field_prefix}.description", risk.description)
    if risk.mitigation:
        _scan_value(f"{field_prefix}.mitigation", risk.mitigation)
    _scan_trigger(field_prefix, risk.trigger)


def assert_no_credential_leak(kf: KnowledgeFile) -> None:
    """Adapter-boundary validator: reject a KnowledgeFile carrying a secret.

    Scans every emitted signal / risk / uncertainty value (and the `auth_state`
    scope, already model-validated) for credential / token / cookie / id / PII
    shapes. A risk is scanned in full via `_scan_risk`: description, mitigation,
    and the structured trigger's `expect`, `action`, AND `body_or_params` dict
    (the POST /login body is where the real password lands in the trace for the
    canonical teach goal). Raises `CredentialLeak` on the first hit so a teach
    emit that baked a secret into an assertion never reaches a committed file
    (ADR-0022 decision 5). `auth_state.authenticated` / `scope` are the only
    authentication facts a confirmed teach session records; the secret that
    produced them is the browser's input, never the knowledge's output.
    """
    for sig in kf.success_signals:
        _scan_value("success_signal.value", sig.value)
    for sig in kf.failure_signals or []:
        _scan_value("failure_signal.value", sig.value)
    for risk in kf.risks or []:
        # Single per-risk scan (description, mitigation, trigger expect / action /
        # body_or_params) shared with the re-teach path so the defense is symmetric.
        _scan_risk("risk", risk)
    for unc in kf.uncertainties or []:
        _scan_value("uncertainty.question", unc.question)


# --- the dual end condition + backstop (ADR-0022 decision 3) ---------------


@dataclass
class TeachBudget:
    """The per-session backstop that bounds a non-converging teach session
    (ADR-0022 decision 3).

    A session carries a per-session action budget AND a wall-clock limit; when
    EITHER is exhausted before the dual end condition is met, the session
    terminates LOUDLY as incomplete. `max_actions` caps the number of browser
    actions the brain may take; `max_wall_seconds` caps the wall time. Either
    bound being `None` disables only that bound; at least one should be set for a
    real session, but a fully unbounded session is allowed for tests that drive
    the loop deterministically.
    """

    max_actions: int | None = None
    max_wall_seconds: float | None = None

    def exhausted(self, *, actions: int, elapsed_seconds: float) -> str | None:
        """Return a reason string if a bound is exhausted, else None.

        The reason names WHICH bound tripped and the numbers, so the
        not-converged event is traceable (loud over silent). Checked after the
        brain returns each step: the brain run is one opaque call the session
        cannot interrupt mid-flight, so an over-budget step is reported after it
        returns, never trusted as convergence (the same post-hoc cap shape the
        regress / explore aggregates use)."""
        if self.max_actions is not None and actions > self.max_actions:
            return f"actions {actions} > budget {self.max_actions}"
        if (
            self.max_wall_seconds is not None
            and elapsed_seconds > self.max_wall_seconds
        ):
            return (
                f"wall {elapsed_seconds:.3f}s > budget "
                f"{self.max_wall_seconds:.3f}s"
            )
        return None


@dataclass
class EndCondition:
    """The dual end condition state (ADR-0022 decision 3).

    A teach session ends SUCCESSFULLY only when BOTH hold:

    - `happy_path_observed`: the brain observed the happy path as a
      believed-grade success signal (ideally behavioral + network diversity).
    - `human_confirmed`: the human answered a confirmation prompt affirming the
      reached state is the intended success (the ADR-0005 human seed act).

    Neither half alone ends the session: an observed-but-unconfirmed path stays
    open, and a confirmation without an observed path is REJECTED (there is no
    signal to seed). `met()` is the single predicate both the session loop and a
    test read.
    """

    happy_path_observed: bool = False
    human_confirmed: bool = False

    def met(self) -> bool:
        return self.happy_path_observed and self.human_confirmed

    def missing(self) -> list[str]:
        """What is still missing, for the not-converged event (loud + named)."""
        gaps: list[str] = []
        if not self.happy_path_observed:
            gaps.append("happy-path-observed")
        if not self.human_confirmed:
            gaps.append("human-confirm")
        return gaps


@dataclass(frozen=True)
class NotConvergedEvent:
    """A loud, traceable record that a teach session did NOT converge
    (ADR-0022 decision 3).

    Emitted when the budget / wall backstop trips before the dual end condition
    is met. The session writes NO goal to `.praxis/knowledge/`; this event names
    what was REACHED and what was MISSING so the failure is visible and the
    session is re-runnable, never a silent empty file. It is the
    `loud-and-traceable-over-silent-and-convenient` half of the end condition.
    """

    kind: str = "teach_not_converged"
    goal_id: str = ""
    goal: str = ""
    reached: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reason: str = ""
    actions: int = 0
    elapsed_seconds: float = 0.0
    ts: datetime = field(default_factory=_utcnow)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly view a run record / log can serialize (no secret)."""
        return {
            "kind": self.kind,
            "goal_id": self.goal_id,
            "goal": self.goal,
            "reached": list(self.reached),
            "missing": list(self.missing),
            "reason": self.reason,
            "actions": self.actions,
            "elapsed_seconds": self.elapsed_seconds,
            "ts": self.ts.isoformat(),
        }

    def message(self) -> str:
        """A one-line loud message naming reached + missing for the operator."""
        reached = ", ".join(self.reached) if self.reached else "nothing"
        missing = ", ".join(self.missing) if self.missing else "nothing"
        return (
            f"teach session for {self.goal_id!r} did NOT converge ({self.reason}): "
            f"reached [{reached}], missing [{missing}]. No goal written; re-run."
        )


# --- the session outcome --------------------------------------------------


@dataclass
class TeachOutcome:
    """The result of a teach session (ADR-0022 decisions 3, 4, 6).

    Exactly one of three shapes is populated:

    - a confirmed NEW goal: `knowledge` is the emitted human-seeded
      `KnowledgeFile` and `knowledge_path` is where it was written under
      `.praxis/knowledge/`.
    - a confirmed RE-TEACH of a believed goal: `candidate_paths` are the
      contested candidate refinement files written under `.praxis/candidates/`
      (no in-place edit), `knowledge` / `knowledge_path` stay None.
    - a non-converged session: `not_converged` is the loud event and nothing was
      written.

    `converged` and `contested_refinement` are convenience flags the skill and
    the tests read without re-deriving which shape this is.
    """

    converged: bool
    contested_refinement: bool = False
    knowledge: KnowledgeFile | None = None
    knowledge_path: Path | None = None
    candidate_paths: list[Path] = field(default_factory=list)
    not_converged: NotConvergedEvent | None = None


# --- the teach session driver ---------------------------------------------


@dataclass
class TeachSession:
    """The non-interactive teach machinery the `/praxis:teach` skill drives.

    Construct it on a discovered project's knowledge + candidates directories
    (the skill builds these from `ProjectContext`; tests build them from a
    tmp_path). The session does NOT drive a browser and does NOT reason: the
    skill (the brain) calls `end_condition`, `record_navigation_hint`, and the
    emit methods as it explores the live app and blocks on human answers. The
    session owns the contract:

    - `goal_already_believed` detects an existing believed goal so the skill can
      route a re-teach into a contested candidate (decision 6).
    - `emit_seed` writes the human-seeded NEW goal YAML (decisions 4, 5).
    - `emit_contested_refinement` appends candidate refinements for a believed
      goal instead of overwriting it (decision 6).
    - `finish` enforces the dual end condition + backstop, returning a
      `TeachOutcome` that either committed the seed / refinement or carries the
      loud not-converged event (decision 3).

    `clock` and `time_source` are injectable so tests drive the wall-time
    backstop deterministically.
    """

    knowledge_dir: Path
    candidates_dir: Path
    target: Target
    confirming_human: str
    observed_app_version: str | None = None
    budget: TeachBudget = field(default_factory=TeachBudget)
    time_source: Any = time.monotonic

    def __post_init__(self) -> None:
        self.knowledge_dir = Path(self.knowledge_dir)
        self.candidates_dir = Path(self.candidates_dir)
        if not self.confirming_human or not isinstance(self.confirming_human, str):
            raise ValueError(
                "confirming_human is required: the human who confirms the success "
                "state is the ADR-0005 seed source (source_type=human)."
            )
        self._started = self.time_source()
        self._candidate_files = CandidateFileStore(self.candidates_dir)

    # ---- believed-goal detection (decision 6) ----------------------------

    def _knowledge_path_for(self, goal_id: str) -> Path:
        return self.knowledge_dir / f"{goal_id}.knowledge.yaml"

    def goal_already_believed(self, goal_id: str) -> bool:
        """True if `goal_id` already exists believed in `.praxis/knowledge/`.

        A teach session refuses to silently overwrite a believed goal (decision
        6): if this returns True, the session routes the re-teach into a
        contested candidate refinement (`emit_contested_refinement`) rather than
        editing the committed seed. "Believed" means the goal's seed file exists
        and at least one success signal is `believed` - the trusted oracle the
        no-silent-overwrite rule protects.
        """
        path = self._knowledge_path_for(goal_id)
        if not path.exists():
            return False
        # Lazy import keeps this module loadable without exercising YAML I/O.
        from ..model import load

        try:
            kf = load(path)
        except Exception:
            # A malformed seed is not a believed oracle; treat as absent so the
            # skill can re-author rather than silently inheriting a broken file.
            return False
        return any(s.status == Status.BELIEVED for s in kf.success_signals)

    # ---- the human seed (decisions 4, 5) ---------------------------------

    def build_seed(
        self,
        *,
        goal_id: str,
        goal: str,
        success_signals: list[Signal],
        failure_signals: list[Signal] | None = None,
        risks: list[Risk] | None = None,
        uncertainties: list[Uncertainty] | None = None,
        auth_state: AuthState | None = None,
    ) -> KnowledgeFile:
        """Assemble the human-seeded `KnowledgeFile` a confirmed session emits.

        The success oracle MUST carry `provenance.source_type = human` (the
        confirming human) - the legitimate ADR-0005 first-oracle seed path
        (decision 4). This method enforces that: a success signal whose
        provenance is `agent` or whose source_id is not the confirming human is
        rejected, so a self-certified oracle cannot masquerade as a seed.
        Provenance + confidence are already mandatory on every Signal / Risk and
        author + timestamp on every Uncertainty by the model (ADR-0004); this
        method additionally enforces the seed-source rule and scans for leaked
        secrets before returning (decision 5).
        """
        if not success_signals:
            raise ValueError(
                "a confirmed teach session must emit at least one success signal "
                "(there is no oracle to seed otherwise; ADR-0005)."
            )
        for s in success_signals:
            if s.provenance.source_type != SourceType.HUMAN:
                raise ValueError(
                    f"teach success oracle must be human-seeded "
                    f"(source_type=human), got "
                    f"{s.provenance.source_type.value!r} for {s.value!r}. The "
                    f"confirming human is the ADR-0005 seed source (decision 4); "
                    f"teach never self-certifies by agent count."
                )
            if s.provenance.source_id != self.confirming_human:
                raise ValueError(
                    f"teach success signal source_id {s.provenance.source_id!r} "
                    f"does not match the confirming human "
                    f"{self.confirming_human!r}. The human who confirmed the "
                    f"success state authors the seed (decision 4)."
                )
        # Validate any emitted risk trigger is structured (ADR-0009 / ADR-0014):
        # a free-text trigger is rejected before it can reach a committed file.
        for r in risks or []:
            self._validate_trigger(r.trigger, where=f"risk {r.id!r}")

        now = _utcnow()
        kf = KnowledgeFile(
            schema_version="0",
            goal_id=goal_id,
            goal=goal,
            target=self.target,
            success_signals=success_signals,
            failure_signals=failure_signals or None,
            risks=risks or None,
            uncertainties=uncertainties or None,
            auth_state=auth_state,
            meta=Meta(
                created_at=now,
                updated_at=now,
                contributing_agents=None,
            ),
        )
        # Last line of defense: no credential / token / cookie / id / PII crossed
        # into any emitted assertion (decision 5). Raises CredentialLeak if so.
        assert_no_credential_leak(kf)
        return kf

    @staticmethod
    def _validate_trigger(trigger: Trigger, *, where: str) -> None:
        outcome = validate_trigger(trigger)
        if outcome.outcome == "rejected":
            raise ValueError(
                f"{where}: trigger rejected ({outcome.reason}). A teach session "
                f"emits structured triggers only (ADR-0009 sec 4, ADR-0014)."
            )

    def human_provenance(
        self,
        *,
        observation_count: int = 1,
        last_verified: datetime | None = None,
    ) -> Provenance:
        """Build the `human` provenance for a teach-seeded signal / risk.

        The confirming human is the source (`source_type=human`,
        `source_id=<confirming_human>`), the legitimate ADR-0005 seed path. The
        skill uses this so every emitted assertion carries mandatory provenance
        (ADR-0004) anchored to the human who confirmed the success state.
        """
        return Provenance(
            source_type=SourceType.HUMAN,
            source_id=self.confirming_human,
            observed_app_version=self.observed_app_version,
            last_verified=last_verified or _utcnow(),
            observation_count=observation_count,
        )

    # ---- the re-teach contested refinement (decision 6) ------------------

    def emit_contested_refinement(
        self,
        *,
        goal_id: str,
        risks: list[Risk] | None = None,
        uncertainties: list[Uncertainty] | None = None,
    ) -> list[Path]:
        """Append a contested candidate refinement for a BELIEVED goal (decision 6).

        When the named goal already exists believed, a re-teach does NOT
        overwrite the committed seed: it appends one ADR-0014 `CandidateEvent`
        per proposed risk / uncertainty into `.praxis/candidates/<goal>/`, each
        contested by default, via the Wave 1 `CandidateFileStore` writer. The
        existing believed knowledge is preserved (ADR-0001); promotion requires
        the same human-seed-via-git-merge path ADR-0018 fixed, never an in-place
        edit. `source_id = agent_identity = confirming_human` matches the
        candidate-event contract (ADR-0014 sec 2); a refinement and the existing
        seed are two events, not a mutation.

        Returns the committed candidate file paths.
        """
        events: list[CandidateEvent] = []
        for r in risks or []:
            self._validate_trigger(r.trigger, where=f"risk {r.id!r}")
            # A re-teach refinement is CONTESTED by default (ADR-0014 sec 2,
            # ADR-0022 decision 6): it never lands as believed, it joins the
            # contested queue for a human to promote via git merge. Force the
            # status to contested and align the risk's provenance source_id with
            # `agent_identity` (the confirming human), which the CandidateEvent
            # contract requires (ADR-0014 sec 2). The original believed seed is
            # untouched; this is a new event, not a mutation.
            refinement = r.model_copy(
                update={
                    "status": Status.CONTESTED,
                    "provenance": r.provenance.model_copy(
                        update={"source_id": self.confirming_human}
                    ),
                }
            )
            events.append(
                CandidateEvent(
                    agent_identity=self.confirming_human,
                    goal_id=goal_id,
                    observed_app_version=self.observed_app_version,
                    payload=CandidateRiskPayload(risk=refinement),
                )
            )
        for u in uncertainties or []:
            # Align `raised_by` with `agent_identity` (the confirming human) so
            # the CandidateEvent contract holds (ADR-0014 sec 2): the candidate's
            # source is the human who re-taught, one source under the diversity
            # rule, never a self-promotion path.
            refinement_u = u.model_copy(update={"raised_by": self.confirming_human})
            events.append(
                CandidateEvent(
                    agent_identity=self.confirming_human,
                    goal_id=goal_id,
                    observed_app_version=self.observed_app_version,
                    payload=CandidateUncertaintyPayload(uncertainty=refinement_u),
                )
            )
        if not events:
            raise ValueError(
                "a re-teach refinement must propose at least one risk or "
                "uncertainty; there is nothing to contest otherwise (decision 6)."
            )
        # Each event's underlying assertion must not carry a secret either. The
        # SAME per-risk scan the seed path uses (`_scan_risk`: description,
        # mitigation, trigger expect / action / body_or_params) runs here, so a
        # secret rejected on a new-goal seed is rejected identically on a re-teach
        # (no asymmetric defense, ADR-0022 decision 5).
        for ev in events:
            if isinstance(ev.payload, CandidateRiskPayload):
                _scan_risk("candidate_risk", ev.payload.risk)
            else:
                _scan_value(
                    "candidate_uncertainty.question",
                    ev.payload.uncertainty.question,
                )
        return self._candidate_files.write_all(events)

    # ---- the dual end condition + backstop (decision 3) ------------------

    def elapsed_seconds(self) -> float:
        return float(self.time_source() - self._started)

    def backstop_reason(self, *, actions: int) -> str | None:
        """The budget / wall reason if the backstop tripped, else None."""
        return self.budget.exhausted(
            actions=actions, elapsed_seconds=self.elapsed_seconds()
        )

    def finish(
        self,
        *,
        goal_id: str,
        goal: str,
        end_condition: EndCondition,
        actions: int = 0,
        seed: KnowledgeFile | None = None,
        refinement_risks: list[Risk] | None = None,
        refinement_uncertainties: list[Uncertainty] | None = None,
    ) -> TeachOutcome:
        """Close the session, enforcing the dual end condition + backstop.

        The single place the three teach outcomes are decided (decision 3):

        1. Backstop tripped before convergence -> write NO goal, return a loud
           `NotConvergedEvent` naming what was reached and what was missing.
        2. Dual end condition NOT met (and backstop not tripped) -> still
           incomplete: write NO goal, return a `NotConvergedEvent`. An observed-
           but-unconfirmed path and a confirmed-but-unobserved path both land
           here; neither half alone ends the session.
        3. Dual end condition met -> commit. If the goal already exists believed,
           append a contested candidate refinement (decision 6) from
           `refinement_risks` / `refinement_uncertainties`. Otherwise write the
           human-seeded NEW goal YAML from `seed`.

        `seed` must be a `build_seed` result (human-seeded, leak-scanned) when
        committing a new goal; a confirmation without an observed path is
        rejected upstream by `end_condition.met()` returning False.
        """
        reached = self._reached(end_condition)
        backstop = self.backstop_reason(actions=actions)

        if backstop is not None and not end_condition.met():
            return self._not_converged(
                goal_id=goal_id, goal=goal, end_condition=end_condition,
                actions=actions, reason=f"backstop exhausted: {backstop}",
                reached=reached,
            )

        if not end_condition.met():
            return self._not_converged(
                goal_id=goal_id, goal=goal, end_condition=end_condition,
                actions=actions, reason="dual end condition not met",
                reached=reached,
            )

        # Converged. Route a believed goal into a contested refinement; emit a
        # fresh human seed otherwise.
        if self.goal_already_believed(goal_id):
            paths = self.emit_contested_refinement(
                goal_id=goal_id,
                risks=refinement_risks,
                uncertainties=refinement_uncertainties,
            )
            return TeachOutcome(
                converged=True,
                contested_refinement=True,
                candidate_paths=paths,
            )

        if seed is None:
            raise ValueError(
                "converged on a NEW goal but no seed was built; call build_seed "
                "first so the emitted oracle is a human seed (decision 4)."
            )
        if seed.goal_id != goal_id:
            raise ValueError(
                f"seed goal_id {seed.goal_id!r} does not match the session goal "
                f"{goal_id!r}."
            )
        # Re-scan at the emit boundary (defense in depth; build_seed already did).
        assert_no_credential_leak(seed)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        out = self._knowledge_path_for(goal_id)
        dump(seed, out)
        return TeachOutcome(
            converged=True,
            knowledge=seed,
            knowledge_path=out,
        )

    @staticmethod
    def _reached(end_condition: EndCondition) -> list[str]:
        reached: list[str] = []
        if end_condition.happy_path_observed:
            reached.append("happy-path-observed")
        if end_condition.human_confirmed:
            reached.append("human-confirm")
        return reached

    def _not_converged(
        self,
        *,
        goal_id: str,
        goal: str,
        end_condition: EndCondition,
        actions: int,
        reason: str,
        reached: list[str],
    ) -> TeachOutcome:
        event = NotConvergedEvent(
            goal_id=goal_id,
            goal=goal,
            reached=reached,
            missing=end_condition.missing(),
            reason=reason,
            actions=actions,
            elapsed_seconds=self.elapsed_seconds(),
        )
        return TeachOutcome(converged=False, not_converged=event)
