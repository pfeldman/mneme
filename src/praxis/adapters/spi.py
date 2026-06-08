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


# --- Redaction (docs/06): never persist secrets, tokens, generated ids, or PII ---

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\b(?:eyJ[\w-]+\.[\w-]+\.[\w-]+)\b"), "<jwt>"),  # JWTs
    (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "<token>"),  # long opaque tokens/ids
    (re.compile(r"\b\d{13,19}\b"), "<card-number>"),  # PANs
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+"),
     r"\1=<redacted>"),
)


def redact(value: str) -> str:
    """Scrub obvious secrets/PII from a free-text signal value.

    This is a Phase-0 best-effort filter, not a guarantee - it errs toward
    over-redacting. Knowledge should describe invariants ("a session cookie is
    set"), never the secret itself ("cookie=abc123"), so redaction here is a safety
    net behind that discipline.
    """
    out = value
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out


def redact_observation(obs: ObservedSignal) -> ObservedSignal:
    """Return a copy of an observation with its value redacted."""
    return obs.model_copy(update={"value": redact(obs.value)})
