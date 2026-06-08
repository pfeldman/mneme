"""Experiment-level wrapper around `praxis.metrics.exploration_reward`.

Composes the locked formula with per-run aggregation + report-rendering
helpers. The formula itself lives in `src/praxis/metrics/exploration_reward.py`;
this module turns a run's projection into the `RewardInputs` and renders
a markdown row.

Scope discipline (ADR-0015):
- Reward is observability-only. Nothing in this module touches agent
  state or feeds back into prompt selection.
- Random-walk and memory arms both compute reward via the same formula.
  Reporting one arm without the other is a forbidden alternative
  (ADR-0015 sec 7); the `report` helper asserts both arms are present.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from praxis.metrics import (
    PRE_REGISTERED_ALPHA,
    RewardComputation,
    RewardInputs,
    RewardSeal,
    compute_reward,
    count_unique_new_risks,
    seal_run,
)
from praxis.model import Risk, Uncertainty


@dataclass(frozen=True)
class ArmRunProjection:
    """A single arm's projection-time inputs for the reward.

    The harness assembles this from event-log queries (resolved uncertainties)
    and store reads (new candidate risks vs existing risks). This dataclass
    is the contract between the harness and the metrics module.
    """

    arm: str  # "memory" | "random_walk" | other
    seed: int
    budget_tokens: int
    resolved_uncertainties_new: list[Uncertainty]
    new_candidate_risks: list[Risk]
    existing_risks: list[Risk]


@dataclass(frozen=True)
class ArmRewardReport:
    """Per-arm output row plus the audit trail (inputs + seal)."""

    arm: str
    seed: int
    computation: RewardComputation
    seal: RewardSeal
    unique_candidates_per_1000_tokens: float


def compute_arm_reward(
    projection: ArmRunProjection,
    *,
    seal: RewardSeal,
) -> ArmRewardReport:
    """Apply the ADR-0015 formula to one arm's projection.

    Counts are derived deterministically:
      - `resolved_uncertainties` from `len(resolved_uncertainties_new)`.
        The harness is responsible for filtering to uncertainties whose
        resolution flip is attributable to E-mode (ADR-0015 sec 1 +
        resolution criterion in `pre_registration.md`).
      - `new_unique_candidate_risks` via the canonicalization rule in
        `src/praxis/metrics/exploration_reward.py`.
    """
    unique_new = count_unique_new_risks(
        projection.new_candidate_risks,
        existing_risks=projection.existing_risks,
    )
    inputs = RewardInputs(
        arm=projection.arm,
        resolved_uncertainties=len(projection.resolved_uncertainties_new),
        new_unique_candidate_risks=unique_new,
        budget_tokens=projection.budget_tokens,
    )
    comp = compute_reward(inputs, alpha=seal.alpha)
    # Sibling metric from ADR-0015 sec 6: unique candidates per 1000 tokens.
    # Reported alongside the reward; below the pre-registered floor the
    # report layer flags the run LOUD.
    if projection.budget_tokens > 0:
        per_1000 = (unique_new * 1000.0) / projection.budget_tokens
    else:
        per_1000 = 0.0
    return ArmRewardReport(
        arm=projection.arm,
        seed=projection.seed,
        computation=comp,
        seal=seal,
        unique_candidates_per_1000_tokens=per_1000,
    )


def compute_experiment_rewards(
    projections: Sequence[ArmRunProjection],
    *,
    praxis_git_sha: str,
    alpha: float = PRE_REGISTERED_ALPHA,
) -> tuple[RewardSeal, list[ArmRewardReport]]:
    """Compute rewards for a whole experiment under ONE sealed alpha.

    All projections in the input MUST be from the same experiment run
    (same `praxis_git_sha`, same alpha). The seal is computed once and
    shared across all per-arm rows; downstream readers that aggregate
    across experiments use `RewardSeal.verify_invariant` to reject
    mixing.
    """
    seal = seal_run(praxis_git_sha=praxis_git_sha, alpha=alpha)
    rows = [compute_arm_reward(p, seal=seal) for p in projections]
    return seal, rows


def write_reward_report(
    rows: Sequence[ArmRewardReport],
    seal: RewardSeal,
    *,
    path: str | Path,
    unique_candidates_floor_per_1000: float = 0.5,
) -> None:
    """Render the per-arm reward table + the LOUD floor flag.

    The floor (`unique_candidates_per_budget`, ADR-0015 sec 6) is a
    sibling observability metric pre-registered alongside the reward.
    The number `0.5/1000` is a placeholder pending a dry-run
    calibration; ADR-0015 commits the existence of the floor, not the
    value. Once calibrated, the value moves into
    `pre_registration.md` and out of this signature.

    ADR-0015 sec 7 forbids reporting `memory` without `random_walk`; we
    enforce by asserting both arm names appear in the rows.
    """
    arms_present = {r.arm for r in rows}
    if "memory" in arms_present and "random_walk" not in arms_present:
        raise ValueError(
            "ADR-0015 sec 7 forbidden: cannot report a memory-arm reward "
            "without the paired random_walk baseline arm. arms found: "
            f"{sorted(arms_present)}"
        )

    out: list[str] = [
        "# Exploration reward report (ADR-0015)",
        "",
        f"seal_id: `{seal.seal_id}`",
        f"praxis_git_sha: `{seal.praxis_git_sha}`",
        f"alpha: `{seal.alpha}`",
        f"canonicalization: `{seal.canonicalization_rule_id}`",
        "",
        "## Per-arm rewards",
        "",
        "| arm | seed | resolved_u | new_unique_risks | budget_tokens "
        "| reward | unique/1000tok | floor_flag |",
        "|-----|------|------------|-------------------|----------------"
        "|--------|----------------|------------|",
    ]
    for r in rows:
        inputs = r.computation.inputs
        floor_flag = (
            "FLOOR" if r.unique_candidates_per_1000_tokens < unique_candidates_floor_per_1000 else "ok"
        )
        out.append(
            f"| `{r.arm}` | {r.seed} | {inputs.resolved_uncertainties} | "
            f"{inputs.new_unique_candidate_risks} | {inputs.budget_tokens} | "
            f"{r.computation.reward:.6f} | "
            f"{r.unique_candidates_per_1000_tokens:.3f} | {floor_flag} |"
        )
    out += [
        "",
        "## Notes",
        "",
        "- Reward is observability-only (ADR-0015 sec 2). The agent does",
        "  NOT see this number; it does NOT feed back into prompt",
        "  selection or budget allocation in Phase 2.",
        "- The random-walk baseline arm is non-optional (ADR-0015 sec 5).",
        "- `goodhart_score` (ADR-0015 sec 6) lands the run AFTER this one;",
        "  see `experiments/exploration_reward/goodhart_attacks.md`.",
        "",
    ]
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
