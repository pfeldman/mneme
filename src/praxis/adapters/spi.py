"""The adapter SPI + the redaction boundary.

The SPI is intentionally tiny and must stay stable (ADR-0003): three methods
since Phase 2 (ADR-0014). Every adapter is also the place where secrets/PII
are stripped before knowledge enters the append-only store - a structural
requirement, since shared memory otherwise leaks one user's data to another
(docs/05, docs/06).

ADR-0014 adds `write_candidates`: persists agent-proposed risks and
uncertainties as `CandidateEvent`s, runs the structured-trigger validator
on candidate risks at the boundary (free-text triggers raise, never silently
believed), and forces `agent_identity` as the canonical source under the
independence rule.

ADR-0017 sec 3 codifies that the adapter is the redaction point for the
Phase-2 `auth_state` projection: adapters strip cookies, tokens, user/session
identifiers, and PII from raw responses before constructing observations.
`redact()` is the runtime defense; the schema/model is the contract that says
what redaction is for.
"""
from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from ..model import KnowledgeFile, Risk, Uncertainty
from ..store import ObservedSignal


@runtime_checkable
class KnowledgeAdapter(Protocol):
    """Bridge between a runtime and the neutral knowledge schema."""

    def read_knowledge(self, goal_id: str) -> KnowledgeFile | None:
        """Hydrate an agent with the believed knowledge for a goal (None if unknown)."""
        ...

    def write_observations(
        self, goal_id: str, agent_id: str, observations: list[ObservedSignal],
        observed_app_version: str | None = None,
    ) -> None:
        """Append what the agent observed to the store, redacted at the boundary."""
        ...

    def write_candidates(
        self,
        goal_id: str,
        agent_identity: str,
        new_risks: list[Risk] | None = None,
        new_uncertainties: list[Uncertainty] | None = None,
        observed_app_version: str | None = None,
    ) -> list[str]:
        """Persist agent-proposed risks and uncertainties as `CandidateEvent`s
        (ADR-0014). Returns the list of event ids actually persisted (rejected
        risks are skipped). `agent_identity` becomes `source_id` under the
        independence rule (ADR-0008 + ADR-0014 sec 2)."""
        ...


# --- Redaction (docs/06 + ADR-0017): never persist secrets, tokens, generated
# ids, or PII. Each pattern is concrete enough that over-redaction is rare on
# semantic descriptions ("a session cookie is set"), but aggressive enough to
# strip raw credentials that an adapter author might forget to scrub.

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JWTs (run BEFORE the generic long-token rule so the placeholder is more
    # specific than `<token>`).
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"), "<jwt>"),
    # Cookie / Set-Cookie header values (Cookie: name=value; ...).
    (re.compile(r"(?i)(?:^|\b)(set-cookie|cookie)\s*[:=]\s*[^\s;]+"),
     r"\1: <cookie>"),
    # Bearer tokens in headers.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]+"), "Bearer <token>"),
    # Generic per-session id-shaped fields (session=...., sid=..., session_id=...).
    (re.compile(r"(?i)\b(session_id|session|sid|sessionid)\s*[:=]\s*[\S]+"),
     r"\1=<session-id>"),
    # User identifiers (user_id=42, account_id=abc).
    (re.compile(r"(?i)\b(user_id|account_id|userid|accountid|uid)\s*[:=]\s*[\S]+"),
     r"\1=<user-id>"),
    # Emails.
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    # PANs (credit-card-shaped numbers).
    (re.compile(r"\b\d{13,19}\b"), "<card-number>"),
    # Named secrets in key=value form (password, secret, api_key, token).
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+"),
     r"\1=<redacted>"),
    # Long opaque token-like strings (fallback). Runs LAST so the named
    # categories above keep their semantic placeholders.
    (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<token>"),
)


def redact(value: str) -> str:
    """Scrub obvious secrets/PII from a free-text signal value.

    This is a Phase-0 best-effort filter, not a guarantee - it errs toward
    over-redacting. Knowledge should describe invariants ("a session cookie is
    set"), never the secret itself ("cookie=abc123"), so redaction here is a safety
    net behind that discipline.

    ADR-0017 sec 3: when an adapter is about to write an observation that feeds
    `auth_state`, this pass strips tokens / cookies / session ids / user ids /
    PII before the observation reaches the store. The schema-side
    `AuthState.scope` validator is the second backstop.
    """
    out = value
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out


def redact_observation(obs: ObservedSignal) -> ObservedSignal:
    """Return a copy of an observation with its value redacted."""
    return obs.model_copy(update={"value": redact(obs.value)})


# --- Auth-state boundary validator (ADR-0017 sec 2) --------------------------

# Substrings whose presence in an observation `value` that an adapter is about
# to write as feeding `auth_state` indicates the writer is trying to durably
# persist credentials. Different from the textual redaction above: that pass
# scrubs values in place; this validator inspects the FIELD NAMES in the value
# and surfaces a loud, traceable rejection so a wrong write is caught at the
# boundary instead of silently rewritten to `<token>`.

_AUTH_OBSERVATION_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "bearer",
    "set-cookie",
    "cookie:",
    "cookie=",
    "session_id",
    "sessionid",
    "sid=",
    "user_id",
    "userid",
    "account_id",
    "accountid",
    "jwt",
    "api_key",
    "apikey",
    "tenant_id",
    "org_id",
    "workspace_id",
)


class AuthStateLeakError(ValueError):
    """Raised at the adapter boundary when an observation feeding `auth_state`
    carries a forbidden field name (token, cookie, session_id, user_id, JWT,
    tenant_id, etc). ADR-0017 sec 2: rejection is loud and traceable so a
    wrong write does not become a silent leak.
    """


def assert_auth_state_observation_safe(
    obs: ObservedSignal, *, raw_value: str | None = None
) -> None:
    """Reject an observation that attempts to durably persist a credential or
    per-user/per-session identifier into the `auth_state` projection.

    `raw_value` is the ORIGINAL value the adapter saw before `redact()` rewrote
    it; passing the raw value catches the case where redaction would have
    scrubbed the literal token but a forbidden field NAME (e.g. `bearer`,
    `session_id=`) is still recognisable as the writer's intent. If
    `raw_value` is None the check falls back to `obs.value`.

    Use at the adapter boundary, only on observations the adapter knows feed
    auth_state (a network signal on a known-protected endpoint, a behavioral
    signal that asserts logged-in posture, etc). General-purpose observations
    keep going through `redact()`; this is for the auth-state surface only.
    """
    candidate = raw_value if raw_value is not None else obs.value
    lowered = candidate.lower()
    for forbidden in _AUTH_OBSERVATION_FORBIDDEN_TOKENS:
        if forbidden in lowered:
            raise AuthStateLeakError(
                f"adapter refused to persist an auth_state observation that "
                f"contains the forbidden token {forbidden!r} (ADR-0017 sec 2). "
                f"auth_state observations describe POSTURE ('a session cookie "
                f"is set'), never the credential itself. rewrite the "
                f"observation to describe the invariant, not the value."
            )
