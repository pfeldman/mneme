"""Banned-phrase validator for `Risk.trigger` predicates.

The discriminated union on `Trigger` (HttpTrigger vs SequenceTrigger) gates
the SHAPE: a stranger to the system can read the trigger and execute the
probe deterministically. But the `expect` field is a free-text predicate,
and ADR-0009 sec 4 promised that free-text would also be rejected. This
module is the cheap deterministic floor: a set of banned phrasings that
historically mark unfalsifiable triggers ("under high load", "sometimes",
"race condition" without a sequence count), plus a tiny structural
check that an HTTP trigger's `path` actually starts with `/`.

Borderline cases (the predicate is technically structured but the
`expect` looks suspect) emit an LLM-judge event - logged in the store,
not silent - so a future review can audit the judgment. The judge prompt
is in `experiments/regression_recall/judge_prompt.txt`.

Used at the adapter boundary; new risks written by E-mode are validated
before they enter the store.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .knowledge import HttpTrigger, Risk, SequenceTrigger, Trigger

# Banned phrases inside an `expect` predicate. Each one is a known
# unfalsifiable phrasing that hides under structured-trigger shape. The
# regex set is intentionally small + concrete; a long list invites
# whack-a-mole, and the judge step exists for cases this misses.
_BANNED_EXPECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bunder\s+(?:high\s+)?load\b", re.IGNORECASE),
    re.compile(r"\bsometimes\b", re.IGNORECASE),
    re.compile(r"\boccasionally\b", re.IGNORECASE),
    re.compile(r"\bintermittently\b", re.IGNORECASE),
    re.compile(r"\brace\s+condition\b", re.IGNORECASE),  # without sequence count
    re.compile(r"\bflaky\b", re.IGNORECASE),
    re.compile(r"\bsporadic", re.IGNORECASE),
    re.compile(r"\bmay\s+(?:or\s+may\s+not|sometimes)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class TriggerValidation:
    """Outcome of validating one trigger.

    `accepted`  - structurally + textually clean; the trigger enters the store.
    `rejected`  - banned phrase or structural failure; refuse to write.
    `judge_required` - structurally clean, suspect phrasing; the store
                       receives the trigger PLUS a logged judgment event.
                       Phase 1 emits a TODO; the live LLM-judge call is
                       wired in Phase 1.5 (the prompt template already
                       exists in experiments/regression_recall/judge_prompt.txt).
    """

    outcome: str  # "accepted" | "rejected" | "judge_required"
    reason: str | None = None


def validate_trigger(trigger: Trigger) -> TriggerValidation:
    """Return a TriggerValidation. Pure; no side effects.

    The adapter is responsible for acting on the outcome: accepted -> persist;
    rejected -> raise; judge_required -> persist AND emit a judgment-needed
    event so a reviewer can audit.
    """
    expect = trigger.expect
    for pattern in _BANNED_EXPECT_PATTERNS:
        if pattern.search(expect):
            return TriggerValidation(
                outcome="rejected",
                reason=(
                    f"`expect` contains the banned phrase {pattern.pattern!r}; "
                    f"rephrase as a concrete observable predicate "
                    f"(see ADR-0009 sec 4)."
                ),
            )
    if isinstance(trigger, HttpTrigger):
        if not trigger.path.startswith("/"):
            return TriggerValidation(
                outcome="rejected",
                reason=f"HTTP trigger path {trigger.path!r} must start with `/`.",
            )
    elif isinstance(trigger, SequenceTrigger):
        if trigger.n < 1:
            return TriggerValidation(
                outcome="rejected",
                reason=f"SequenceTrigger n={trigger.n} must be >= 1.",
            )
    # The judge step is a placeholder for "looks fine to the regex, but a
    # human / LLM reviewer might disagree". A future Phase-1.5 hook can flip
    # specific structural patterns to judge_required (e.g. an HTTP trigger
    # whose `expect` lacks a concrete numeric / boolean / structured token).
    return TriggerValidation(outcome="accepted")


def validate_risk(risk: Risk) -> TriggerValidation:
    """Convenience wrapper: validate the risk's trigger."""
    return validate_trigger(risk.trigger)
