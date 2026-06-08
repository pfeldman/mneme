"""ADR-0015 exploration-reward tests.

Pin the three behavioral promises:
  - the formula is deterministic given fixed inputs (sec 1);
  - canonicalization deduplicates paraphrases via the trigger validator (sec 3);
  - changing alpha (or any sealed parameter) changes the seal id so prior
    runs cannot silently aggregate with new ones (sec 7).

These tests are intentionally narrow: the reward is observability-only and
the larger experiment integration belongs in
`experiments/exploration_reward/`. What we lock here is the math + the
canonical-key contract + the pre-registration discipline.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from praxis.metrics import (
    PRE_REGISTERED_ALPHA,
    RewardInputs,
    RewardSeal,
    canonical_risk_key,
    canonical_trigger_key,
    compute_reward,
    count_unique_new_risks,
    seal_run,
)
from praxis.model import (
    HttpTrigger,
    Provenance,
    Risk,
    SequenceTrigger,
    SourceType,
    Status,
)


def _prov() -> Provenance:
    return Provenance(
        source_type=SourceType.AGENT,
        source_id="agent-x",
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )


def _http_risk(
    *,
    rid: str = "r",
    method: str = "POST",
    path: str = "/coupon/apply",
    body: dict | None = None,
    expect: str = "returns 200 with applied=true",
) -> Risk:
    return Risk(
        id=rid,
        description="coupon apply probe",
        trigger=HttpTrigger(method=method, path=path, body_or_params=body, expect=expect),  # type: ignore[arg-type]
        provenance=_prov(),
        confidence=0.5,
        status=Status.CONTESTED,
    )


def _seq_risk(*, rid: str = "rs", n: int = 2, action: str = "submit checkout") -> Risk:
    return Risk(
        id=rid,
        description="idempotency probe",
        trigger=SequenceTrigger(n=n, action=action, expect="same order_id both times"),
        provenance=_prov(),
        confidence=0.5,
        status=Status.CONTESTED,
    )


# ----------------------------------------------------------------------- formula


def test_reward_formula_is_deterministic_for_fixed_inputs() -> None:
    """ADR-0015 sec 1: same inputs => same number, always."""
    inputs = RewardInputs(
        arm="memory",
        resolved_uncertainties=2,
        new_unique_candidate_risks=4,
        budget_tokens=1000,
    )
    a = compute_reward(inputs).reward
    b = compute_reward(inputs).reward
    # (2 + 0.5 * 4) / 1000 = 4 / 1000 = 0.004
    assert a == b == pytest.approx(0.004)


def test_reward_alpha_zero_drops_candidate_term() -> None:
    inputs = RewardInputs(
        arm="memory",
        resolved_uncertainties=3,
        new_unique_candidate_risks=100,
        budget_tokens=500,
    )
    out = compute_reward(inputs, alpha=0.0)
    # candidate term is zeroed; only resolved_uncertainties survives.
    assert out.reward == pytest.approx(3 / 500)


def test_reward_zero_budget_is_safe() -> None:
    """A zero-budget run reports reward=0 instead of raising; ADR-0015 sec 6
    relies on the report being able to surface the invalid-input warning."""
    inputs = RewardInputs(
        arm="memory",
        resolved_uncertainties=5,
        new_unique_candidate_risks=5,
        budget_tokens=0,
    )
    assert compute_reward(inputs).reward == 0.0


def test_reward_carries_inputs_and_alpha_for_audit() -> None:
    inputs = RewardInputs(
        arm="random_walk",
        resolved_uncertainties=1,
        new_unique_candidate_risks=1,
        budget_tokens=10,
    )
    out = compute_reward(inputs, alpha=0.5)
    # The computation object lets a reviewer recompute the number from
    # `inputs` + `alpha` and flag drift.
    assert out.inputs is inputs
    assert out.alpha == 0.5
    assert out.reward == pytest.approx(1.5 / 10)


# --------------------------------------------------------------- canonicalization


def test_canonical_key_dedupes_param_order_paraphrase() -> None:
    """ADR-0015 sec 3 example: same HTTP trigger with reordered params
    canonicalizes to the same key."""
    a = _http_risk(rid="a", body={"coupon": "SAVE10", "subtotal": 49})
    b = _http_risk(rid="b", body={"subtotal": 49, "coupon": "SAVE10"})
    assert canonical_risk_key(a) == canonical_risk_key(b)


def test_canonical_key_distinguishes_different_endpoints() -> None:
    a = _http_risk(rid="a", path="/coupon/apply")
    b = _http_risk(rid="b", path="/checkout/submit")
    assert canonical_risk_key(a) != canonical_risk_key(b)


def test_canonical_key_distinguishes_different_methods() -> None:
    a = _http_risk(rid="a", method="GET")
    b = _http_risk(rid="b", method="POST")
    assert canonical_risk_key(a) != canonical_risk_key(b)


def test_canonical_key_ignores_expect_text() -> None:
    """The `expect` predicate is free text and varies across agents; ADR-0015
    sec 3 anchors uniqueness to the structured shape so gaming `expect`
    cannot inflate the score."""
    a = _http_risk(rid="a", expect="returns 200 with applied=true")
    b = _http_risk(rid="b", expect="responds with HTTP 200 and applied flag set")
    assert canonical_risk_key(a) == canonical_risk_key(b)


def test_canonical_key_handles_sequence_triggers() -> None:
    a = _seq_risk(rid="a", n=2, action="submit checkout")
    b = _seq_risk(rid="b", n=2, action="Submit  Checkout")  # whitespace + case noise
    c = _seq_risk(rid="c", n=3, action="submit checkout")
    assert canonical_risk_key(a) == canonical_risk_key(b)
    assert canonical_risk_key(a) != canonical_risk_key(c)


# ------------------------------------------------------------- unique counting


def test_unique_count_dedupes_paraphrases_within_run() -> None:
    """Writing the same trigger twice in one run counts as ONE new unique."""
    new_risks = [
        _http_risk(rid="r1", body={"coupon": "SAVE10", "subtotal": 49}),
        _http_risk(rid="r2", body={"subtotal": 49, "coupon": "SAVE10"}),
    ]
    assert count_unique_new_risks(new_risks) == 1


def test_unique_count_subtracts_existing_risks() -> None:
    existing = [_http_risk(rid="old", body={"coupon": "X"})]
    new_risks = [
        _http_risk(rid="r1", body={"coupon": "X"}),       # duplicate of existing
        _http_risk(rid="r2", body={"coupon": "Y"}),       # genuinely new
    ]
    assert count_unique_new_risks(new_risks, existing_risks=existing) == 1


def test_unique_count_rejects_invalid_triggers() -> None:
    """ADR-0015 sec 3: a risk that fails the ADR-0009 validator cannot be
    counted toward `new_unique_candidate_risks`. The banned phrase
    'sometimes' in `expect` is exactly the schema-rot vector the validator
    rejects."""
    new_risks = [
        _http_risk(rid="r1", expect="sometimes returns 200"),  # rejected
        _http_risk(rid="r2", expect="returns 200 with applied=true"),  # ok
    ]
    assert count_unique_new_risks(new_risks) == 1


def test_unique_count_handles_empty_inputs() -> None:
    assert count_unique_new_risks([]) == 0
    assert count_unique_new_risks([], existing_risks=[]) == 0


# ---------------------------------------------------------------------- sealing


def test_seal_is_deterministic_for_fixed_params() -> None:
    a = seal_run(praxis_git_sha="abc123")
    b = seal_run(praxis_git_sha="abc123")
    assert a.seal_id == b.seal_id
    # Cross-verifying two identical seals does not raise.
    a.verify_invariant(b)


def test_alpha_change_invalidates_seal() -> None:
    """ADR-0015 sec 7: changing alpha after a run starts invalidates prior
    data. We materialize that by making the seal id sensitive to alpha:
    two runs with different alpha have different seal ids and
    `verify_invariant` raises."""
    base = seal_run(praxis_git_sha="abc123", alpha=PRE_REGISTERED_ALPHA)
    drifted = seal_run(praxis_git_sha="abc123", alpha=PRE_REGISTERED_ALPHA + 0.1)
    assert base.seal_id != drifted.seal_id
    with pytest.raises(ValueError, match="RewardSeal mismatch"):
        base.verify_invariant(drifted)


def test_resolution_criterion_change_invalidates_seal() -> None:
    base = seal_run(praxis_git_sha="abc123")
    drifted = seal_run(
        praxis_git_sha="abc123",
        resolution_criterion="some looser criterion",
    )
    assert base.seal_id != drifted.seal_id


def test_git_sha_change_invalidates_seal() -> None:
    """Praxis source moved => the canonicalization implementation may have
    moved. The seal id reflects that and two seals across SHAs cannot
    be silently aggregated."""
    a = seal_run(praxis_git_sha="aaaaaaa")
    b = seal_run(praxis_git_sha="bbbbbbb")
    assert a.seal_id != b.seal_id
    with pytest.raises(ValueError):
        a.verify_invariant(b)


def test_seal_default_alpha_is_pre_registered_value() -> None:
    """ADR-0015 sec 1 records alpha=0.5 as the initial pre-registered value.
    The default must match so a session that calls `seal_run()` without
    arguments cannot drift away from the ADR."""
    seal: RewardSeal = seal_run(praxis_git_sha="abc123")
    assert seal.alpha == PRE_REGISTERED_ALPHA == 0.5


# ----------------------------------------------------------- trigger canonicality


def test_canonical_trigger_key_normalizes_path_case_and_trailing_slash() -> None:
    """Path case + trailing slash should not cause a paraphrase miss.

    The model already constrains `method` to uppercase via Literal, so the
    paraphrase axis we test here is path (mixed case / trailing slash).
    """
    a = HttpTrigger(method="POST", path="/Coupon/Apply/", expect="returns 200")
    b = HttpTrigger(method="POST", path="/coupon/apply", expect="returns 200")
    assert canonical_trigger_key(a) == canonical_trigger_key(b)
