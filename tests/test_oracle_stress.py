"""Adversarial oracle stress (offline). Pins the poisoning resistance that ADR-0005
targets, and DOCUMENTS the two known gaps (type-diversity without source-independence)
so a future fix is forced to update this test. See experiments/ui-mutation/oracle_stress.py
and ADR-0008."""
from __future__ import annotations

import importlib

stress = importlib.import_module("oracle_stress")


def test_all_poisoning_attacks_resisted_and_real_evidence_accepted() -> None:
    r = stress.run()
    # No false belief in any RESIST scenario, including single-source self-
    # corroboration (closed by ADR-0008) and correlated agents up to N=100.
    assert r["resist_breaches"] == []
    # Real, source- and type-diverse evidence is still promoted (no over-paranoia).
    assert r["positive_control_believed"] >= 1
    assert r["PASSED"] is True


def test_correlated_agents_never_create_independence() -> None:
    # Even 100 agents asserting the same single-type signal must not be believed.
    assert stress.s_correlated_same_type(100) == 0


def test_single_source_cannot_self_corroborate_across_types() -> None:
    # The ADR-0008 fix: one source asserting two evidence types is NOT believed.
    assert stress.s_single_source_two_types() == 0


def test_seed_plus_single_agent_is_the_inherent_trust_boundary() -> None:
    # A seed + a single different-type agent IS believed — structurally identical to
    # legitimate cold-start corroboration; indistinguishable from honest observation.
    # Documented as inherent (ADR-0008), mitigated temporally, not at promotion.
    assert stress.s_seed_rides_single_agent() == 1


def test_seed_plus_same_type_paraphrase_stream_does_not_self_certify() -> None:
    # ADR-0029 defect B: a seed plus a stream of same-type single-agent paraphrases
    # must promote NONE of the paraphrases (no different-type partner from a
    # different source). The believed set stays the seed only.
    assert stress.s_seed_plus_paraphrase_stream() == 0
    # And the fix must not over-correct: the seed itself stays believed.
    assert stress.s_seed_survives_paraphrase_stream() == 1
