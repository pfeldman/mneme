"""ADR-0015: pre-registered, observability-only exploration reward.

Formula, locked verbatim from ADR-0015 sec 1:

    reward = (resolved_uncertainties + alpha * new_unique_candidate_risks) / budget_tokens

Where:
- `resolved_uncertainties` = count of `uncertainties` entries that flipped from
  open to resolved during the run, attributable to E-mode observations.
- `new_unique_candidate_risks` = count of new candidate risks written by this
  run that are not duplicates of an existing risk (any status), under the
  canonicalization rule below.
- `alpha` = pre-registered constant, sealed at run-start under
  `praxis_git_sha`. Initial value 0.5 (ADR-0015).
- `budget_tokens` = the token budget the run consumed (same denominator
  Phase 1's `cold_readme` arm used as cost axis).

Canonicalization (ADR-0015 sec 3): two candidate risks are the SAME risk if
their `trigger` fields canonicalize to the same structured form under the
ADR-0009 trigger validator. This module does NOT introduce a new lexer or
normalization rule; it composes the existing validator with deterministic
structural keying. A trigger that fails the ADR-0009 validator does not
enter the candidate set (so it cannot inflate the score).

Observability-only (ADR-0015 sec 2): nothing in this module feeds back into
agent state, prompt selection, or budget allocation. The runtime invariant
is enforced by where this module is called from (the report layer in
`src/praxis/runner/report.py` and `experiments/exploration_reward/`); the
type signatures here make it impossible to accidentally close the loop -
`compute_reward` takes plain counts and returns a plain float, not an
agent-facing object.

Pre-registration discipline (ADR-0009 precedent): changing `alpha`, the
canonicalization rule, or the resolution criteria after a run starts
invalidates prior data. `seal_run` records all three under
`praxis_git_sha`; the verification helper `RewardSeal.verify_invariant`
fails LOUD when a downstream report tries to mix runs with different
seals.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..model import Risk, Trigger
from ..model.knowledge import HttpTrigger, SequenceTrigger
from ..model.trigger_validator import validate_trigger

# ADR-0015 sec 1: alpha is pre-registered at 0.5. Sealed under praxis_git_sha
# at run-start by `seal_run`. Changing this constant counts as an alpha
# change for the purposes of ADR-0015 sec 7 (invalidates prior runs).
PRE_REGISTERED_ALPHA: float = 0.5

# ADR-0015 sec 3: "uncertainty resolved" means the open->resolved flip is
# attributable to E-mode observations via the event log. This module
# accepts the count as an integer because the projection is what decides
# attribution; we do not re-implement projection here.
DEFAULT_RESOLUTION_CRITERION: str = (
    "Uncertainty.resolved transitions False->True during the run, with the "
    "resolving_signal_value populated by an ObservationEvent emitted by "
    "the E-mode runner (agent_id == agent_identity per ADR-0008)."
)


# --------------------------------------------------------------------- canonical


def _canonical_body_or_params(value: dict[str, Any] | None) -> str:
    """Deterministic key for an HttpTrigger's `body_or_params`.

    JSON with sorted keys gives the same string for
    `{coupon: "SAVE10", subtotal: 49}` and
    `{subtotal: 49, coupon: "SAVE10"}` (ADR-0015 sec 3 example). Nested
    dicts are recursively sorted by `json.dumps(sort_keys=True)`.
    """
    if value is None:
        return "null"
    # `sort_keys=True` plus `separators` removes formatting variance so two
    # equivalent payloads produce one key.
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def canonical_trigger_key(trigger: Trigger) -> str:
    """Return a deterministic structural key for a trigger.

    Two triggers with the same key are considered the SAME risk for the
    purposes of `new_unique_candidate_risks` counting. The key omits the
    free-text `expect` predicate by design: ADR-0015 sec 3 anchors
    uniqueness to the structured shape so syntactic gaming of `expect`
    cannot inflate the score.

    Triggers that fail the ADR-0009 validator are NOT keyable: pass them
    through `validate_trigger` first and skip rejected ones. This
    function is purely structural; it does not re-run the validator.
    """
    if isinstance(trigger, HttpTrigger):
        return (
            "http|"
            f"{trigger.method.upper()}|"
            f"{trigger.path.rstrip('/').lower() or '/'}|"
            f"{_canonical_body_or_params(trigger.body_or_params)}"
        )
    if isinstance(trigger, SequenceTrigger):
        # `action` is an intent string ("submit checkout"). Lowercase +
        # whitespace-collapse is sufficient to dedupe paraphrases at the
        # whitespace/case level WITHOUT introducing a lexer (ADR-0015 sec 3
        # forbids substituting a looser uniqueness rule).
        normalized_action = " ".join(trigger.action.lower().split())
        return f"sequence|{trigger.n}|{normalized_action}"
    raise TypeError(f"unknown trigger kind: {type(trigger)!r}")


def canonical_risk_key(risk: Risk) -> str:
    """Convenience wrapper: canonical key for a Risk via its trigger."""
    return canonical_trigger_key(risk.trigger)


def count_unique_new_risks(
    new_risks: list[Risk],
    *,
    existing_risks: list[Risk] | None = None,
) -> int:
    """Count new risks whose canonical key is not already present.

    Rules (ADR-0015 sec 3):
    - A new risk that fails the ADR-0009 trigger validator does not count.
    - A new risk whose canonical key matches an `existing_risks` entry
      does not count (existing covers any status: believed, contested,
      stale, quarantined).
    - Among the new risks themselves, duplicates collapse: writing the
      same trigger twice in one run counts once.

    The function returns an integer count; callers handle the float
    arithmetic in `compute_reward`.
    """
    existing_keys: set[str] = set()
    for r in existing_risks or []:
        # Existing risks live in the store and have already passed the
        # validator at write time; key them directly. We still skip any
        # that the validator currently rejects (defensive: validator may
        # have tightened since the risk was written).
        if validate_trigger(r.trigger).outcome == "rejected":
            continue
        existing_keys.add(canonical_risk_key(r))

    seen_in_run: set[str] = set()
    unique = 0
    for r in new_risks:
        if validate_trigger(r.trigger).outcome == "rejected":
            # ADR-0015 sec 3: rejected triggers cannot enter the count.
            continue
        key = canonical_risk_key(r)
        if key in existing_keys or key in seen_in_run:
            continue
        seen_in_run.add(key)
        unique += 1
    return unique


# --------------------------------------------------------------------- formula


@dataclass(frozen=True)
class RewardInputs:
    """The four integer/numeric inputs to the ADR-0015 formula.

    Counts come from the projection layer (uncertainties resolved during
    the run, new unique candidate risks). The runtime arm name is carried
    so the report can label `memory` vs `random_walk` without renaming
    fields; the value does not enter the formula.
    """

    arm: str
    resolved_uncertainties: int
    new_unique_candidate_risks: int
    budget_tokens: int


@dataclass(frozen=True)
class RewardComputation:
    """Reward plus the inputs that produced it.

    Carrying the inputs alongside the float makes the run record auditable:
    a reader can recompute the value with the formula in this module and
    flag any drift.
    """

    inputs: RewardInputs
    alpha: float
    reward: float


def compute_reward(inputs: RewardInputs, *, alpha: float = PRE_REGISTERED_ALPHA) -> RewardComputation:
    """Apply the ADR-0015 formula. Deterministic; no I/O, no clock.

    `budget_tokens <= 0` is treated as undefined and returns reward=0.0
    with a flat structure (instead of raising) so the report can still
    render the run with a "budget_tokens unrecorded" warning rather than
    crashing the report build. ADR-0015 sec 6 also requires reporting
    when the inputs are invalid; raising would suppress that.
    """
    if inputs.budget_tokens <= 0:
        reward_value = 0.0
    else:
        numerator = (
            inputs.resolved_uncertainties
            + alpha * inputs.new_unique_candidate_risks
        )
        reward_value = numerator / inputs.budget_tokens
    return RewardComputation(inputs=inputs, alpha=alpha, reward=reward_value)


# --------------------------------------------------------------------- sealing


@dataclass(frozen=True)
class RewardSeal:
    """The pre-registration record sealed at run-start (ADR-0015 sec 7).

    Pinning the formula parameters under `praxis_git_sha` is the same
    discipline ADR-0009 imposed on the regression-recall prompt: any
    change after the run starts invalidates the run's data. `seal_id`
    is the hash of the sealed fields; two runs whose seals diverge
    cannot be aggregated.
    """

    praxis_git_sha: str
    alpha: float
    canonicalization_rule_id: str
    resolution_criterion: str
    formula: str
    seal_id: str = field(init=False)

    def __post_init__(self) -> None:
        payload = json.dumps(
            {
                "praxis_git_sha": self.praxis_git_sha,
                "alpha": self.alpha,
                "canonicalization_rule_id": self.canonicalization_rule_id,
                "resolution_criterion": self.resolution_criterion,
                "formula": self.formula,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        # Frozen dataclass: use object.__setattr__ for the derived field.
        object.__setattr__(self, "seal_id", digest)

    def verify_invariant(self, other: RewardSeal) -> None:
        """Raise if two seals disagree (ADR-0015 sec 7 enforcement).

        Two run reports with different seals cannot be aggregated into one
        verdict; the comparison axis would be undefined. This helper is
        the LOUD-and-traceable side of "alpha is sealed".
        """
        if self.seal_id != other.seal_id:
            raise ValueError(
                "RewardSeal mismatch: cannot aggregate runs with different "
                f"sealed parameters. self.seal_id={self.seal_id} "
                f"other.seal_id={other.seal_id}"
            )


# The string IDs below are sealed alongside alpha. They name the rule
# without embedding it, so a future ADR can extend canonicalization with
# a new rule_id and the old runs stay verifiable under their old seal.
CANONICALIZATION_RULE_ID: str = "adr-0015-sec-3-trigger-validator-v1"
FORMULA_STATEMENT: str = (
    "reward = (resolved_uncertainties + alpha * new_unique_candidate_risks) "
    "/ budget_tokens"
)


def seal_run(
    *,
    praxis_git_sha: str,
    alpha: float = PRE_REGISTERED_ALPHA,
    resolution_criterion: str = DEFAULT_RESOLUTION_CRITERION,
) -> RewardSeal:
    """Build the run-start seal.

    Call this exactly once per run, before any reward number is computed.
    The seal goes into the run manifest alongside `praxis_git_sha` so
    later readers can verify the alpha + canonicalization rule + formula
    that produced the reported number. Changing the defaults after a run
    starts is the ADR-0015 sec 7 invalidation event.
    """
    return RewardSeal(
        praxis_git_sha=praxis_git_sha,
        alpha=alpha,
        canonicalization_rule_id=CANONICALIZATION_RULE_ID,
        resolution_criterion=resolution_criterion,
        formula=FORMULA_STATEMENT,
    )
