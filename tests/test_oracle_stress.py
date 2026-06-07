"""Adversarial oracle stress (offline). Pins the poisoning resistance that ADR-0005
targets, and DOCUMENTS the two known gaps (type-diversity without source-independence)
so a future fix is forced to update this test. See experiments/ui-mutation/oracle_stress.py
and ADR-0008."""
from __future__ import annotations

import importlib

stress = importlib.import_module("oracle_stress")


def test_core_resists_adr0005_attacks_and_accepts_real_evidence() -> None:
    r = stress.run()
    # No false belief in any RESIST scenario (lone type, correlated agents up to
    # 100, contradiction, oscillation, stale).
    assert r["resist_breaches"] == []
    # Real, source- and type-diverse evidence is still promoted (no over-paranoia).
    assert r["positive_control_believed"] >= 1
    assert r["CORE_PASSED"] is True


def test_correlated_agents_never_create_independence() -> None:
    # Even 100 agents asserting the same single-type signal must not be believed.
    assert stress.s_correlated_same_type(100) == 0


def test_known_gaps_are_present_until_phase1_hardening() -> None:
    # These DOCUMENT current behavior: a single source asserting two evidence types
    # (or riding a seed's diversity) is wrongly promoted. When the Phase-1 fix lands
    # (require source-independence), flip these to == 0 and update ADR-0008.
    assert stress.s_single_source_two_types() > 0
    assert stress.s_seed_rides_single_agent() > 0
