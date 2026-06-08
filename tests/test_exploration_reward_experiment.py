"""Experiment-level reward integration tests (`experiments/exploration_reward/`).

These pin the harness contract: projection -> reward row, both arms
required, seal carried alongside the row for audit. The core formula
tests live in `tests/test_exploration_reward.py`; this file tests the
wrapper that downstream Phase 2 experiments consume.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from exploration_reward import metrics as exp_metrics
from praxis.model import (
    HttpTrigger,
    Provenance,
    Risk,
    SourceType,
    Status,
    Uncertainty,
)


def _prov() -> Provenance:
    return Provenance(
        source_type=SourceType.AGENT,
        source_id="agent-x",
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )


def _risk(rid: str, path: str = "/x", body: dict | None = None) -> Risk:
    return Risk(
        id=rid,
        description="probe",
        trigger=HttpTrigger(method="POST", path=path, body_or_params=body, expect="returns 200"),  # type: ignore[arg-type]
        provenance=_prov(),
        confidence=0.5,
        status=Status.CONTESTED,
    )


def _uncertainty(uid: str, resolved: bool) -> Uncertainty:
    return Uncertainty(
        id=uid,
        question="q",
        raised_by="agent-x",
        raised_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
        resolved=resolved,
    )


def test_compute_experiment_rewards_shares_one_seal() -> None:
    """Every row in one experiment is sealed under the same alpha + git_sha."""
    memory = exp_metrics.ArmRunProjection(
        arm="memory",
        seed=0,
        budget_tokens=1000,
        resolved_uncertainties_new=[_uncertainty("u1", True)],
        new_candidate_risks=[_risk("r1", path="/a")],
        existing_risks=[],
    )
    random_walk = exp_metrics.ArmRunProjection(
        arm="random_walk",
        seed=0,
        budget_tokens=1000,
        resolved_uncertainties_new=[],
        new_candidate_risks=[],
        existing_risks=[],
    )
    seal, rows = exp_metrics.compute_experiment_rewards(
        [memory, random_walk], praxis_git_sha="abc123"
    )
    assert len(rows) == 2
    for r in rows:
        assert r.seal.seal_id == seal.seal_id


def test_memory_must_be_paired_with_random_walk_in_report(tmp_path: Path) -> None:
    """ADR-0015 sec 7: a memory-arm reward reported without the random_walk
    baseline arm is uninterpretable by construction. The report writer
    raises rather than letting that slip."""
    memory_only = exp_metrics.ArmRunProjection(
        arm="memory",
        seed=0,
        budget_tokens=1000,
        resolved_uncertainties_new=[],
        new_candidate_risks=[],
        existing_risks=[],
    )
    seal, rows = exp_metrics.compute_experiment_rewards(
        [memory_only], praxis_git_sha="abc123"
    )
    with pytest.raises(ValueError, match="random_walk"):
        exp_metrics.write_reward_report(rows, seal, path=tmp_path / "report.md")


def test_report_writes_both_arms_when_paired(tmp_path: Path) -> None:
    memory = exp_metrics.ArmRunProjection(
        arm="memory",
        seed=0,
        budget_tokens=1000,
        resolved_uncertainties_new=[_uncertainty("u1", True)],
        new_candidate_risks=[_risk("r1", path="/a"), _risk("r2", path="/b")],
        existing_risks=[],
    )
    random_walk = exp_metrics.ArmRunProjection(
        arm="random_walk",
        seed=0,
        budget_tokens=1000,
        resolved_uncertainties_new=[],
        new_candidate_risks=[_risk("rw", path="/c")],
        existing_risks=[],
    )
    seal, rows = exp_metrics.compute_experiment_rewards(
        [memory, random_walk], praxis_git_sha="abc123"
    )
    out_path = tmp_path / "report.md"
    exp_metrics.write_reward_report(rows, seal, path=out_path)
    body = out_path.read_text(encoding="utf-8")
    assert "`memory`" in body
    assert "`random_walk`" in body
    assert seal.seal_id in body
    # Reward for memory: (1 resolved + 0.5 * 2 new unique) / 1000 = 0.002
    assert "0.002000" in body


def test_arm_reward_unique_candidates_per_1000_tokens() -> None:
    """Sibling observability metric: unique candidates per 1000 tokens
    (ADR-0015 sec 6). The pre-registered floor will eventually catch
    runs below this rate."""
    arm = exp_metrics.ArmRunProjection(
        arm="memory",
        seed=0,
        budget_tokens=10_000,
        resolved_uncertainties_new=[],
        new_candidate_risks=[_risk("r1", path="/a"), _risk("r2", path="/b")],
        existing_risks=[],
    )
    seal, rows = exp_metrics.compute_experiment_rewards([arm], praxis_git_sha="abc")
    assert len(rows) == 1
    # 2 unique candidates in 10k tokens -> 0.2 per 1000 tokens.
    assert rows[0].unique_candidates_per_1000_tokens == pytest.approx(0.2)


def test_random_walk_baseline_uses_same_formula() -> None:
    """ADR-0015 sec 5: both arms compute reward with the same formula.
    Same inputs -> same reward regardless of arm name."""
    memory = exp_metrics.ArmRunProjection(
        arm="memory",
        seed=0,
        budget_tokens=1000,
        resolved_uncertainties_new=[_uncertainty("u1", True)],
        new_candidate_risks=[_risk("r1", path="/a")],
        existing_risks=[],
    )
    random_walk = exp_metrics.ArmRunProjection(
        arm="random_walk",
        seed=0,
        budget_tokens=1000,
        resolved_uncertainties_new=[_uncertainty("u2", True)],
        new_candidate_risks=[_risk("r2", path="/a")],  # same canonical key
        existing_risks=[],
    )
    seal, rows = exp_metrics.compute_experiment_rewards(
        [memory, random_walk], praxis_git_sha="abc"
    )
    # Identical inputs (1 resolved, 1 new unique, 1000 budget) -> identical reward.
    memory_row = next(r for r in rows if r.arm == "memory")
    rw_row = next(r for r in rows if r.arm == "random_walk")
    assert memory_row.computation.reward == rw_row.computation.reward
