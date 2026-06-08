"""Pre-registered observability metrics.

This package hosts numeric metrics that report on agent behavior without
feeding back into agent state. The exploration reward (ADR-0015) lives here;
its inputs are pre-registered, sealed under `praxis_git_sha` at run-start,
and the formula is locked. Reporting a number IS optimizing for it, so the
parameters that define what the number means are deliberately frozen.

Public API:
    canonical_trigger_key      - structural canonicalization for ADR-0015 uniqueness
    canonical_risk_key         - convenience wrapper over Risk
    count_unique_new_risks     - dedupe new risks against existing risks
    RewardInputs, RewardSeal   - dataclasses for the reward computation
    compute_reward             - the locked formula from ADR-0015 sec 1
    seal_run                   - build a RewardSeal pinned to praxis_git_sha
    PRE_REGISTERED_ALPHA       - the alpha=0.5 ADR-0015 ships with
"""
from __future__ import annotations

from .exploration_reward import (
    PRE_REGISTERED_ALPHA,
    RewardComputation,
    RewardInputs,
    RewardSeal,
    canonical_risk_key,
    canonical_trigger_key,
    compute_reward,
    count_unique_new_risks,
    seal_run,
)

__all__ = [
    "PRE_REGISTERED_ALPHA",
    "RewardComputation",
    "RewardInputs",
    "RewardSeal",
    "canonical_risk_key",
    "canonical_trigger_key",
    "compute_reward",
    "count_unique_new_risks",
    "seal_run",
]
