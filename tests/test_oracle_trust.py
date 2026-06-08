"""The oracle is sacred (ADR-0005). These tests pin the diversity-or-seed rule, the
cold-start seed, and flip-flop quarantine — the logic that keeps shared memory from
accumulating confident lies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from praxis.model import SignalType, SourceType, Status
from praxis.oracle import (
    SignalSummary,
    agreeing_types,
    classify,
    confidence_of,
    has_contradiction,
    is_flip_flop,
    oracle_believed,
    summary_corroborated,
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def summary(type_, *, kind="success", present=(True,), seeded=False, ver="1",
            last=NOW, source=None) -> SignalSummary:
    s = SignalSummary(kind=kind, type=type_, value=f"{type_} signal")
    s.presence = list(present)
    s.last_verified = last
    s.observed_app_versions = {ver}
    s.source_types = {SourceType.SPEC} if seeded else {SourceType.AGENT}
    s.source_ids = {source or ("spec" if seeded else "agent-1")}
    return s


# --- the diversity-or-seed gate ---------------------------------------------

def test_single_agent_type_is_not_believed() -> None:
    """Two runs of the same model are not independent: one evidence type, however
    many observations, never satisfies the oracle."""
    s = summary(SignalType.BEHAVIORAL, present=(True, True, True, True))
    assert oracle_believed([s]) is False
    assert classify(s, oracle_independent=False, agreeing={SignalType.BEHAVIORAL},
                    now=NOW, current_version="1") is Status.CONTESTED


def test_two_different_types_from_different_sources_are_believed() -> None:
    a = summary(SignalType.BEHAVIORAL, source="agent-1")
    b = summary(SignalType.NETWORK, source="agent-2")
    assert oracle_believed([a, b]) is True
    agreeing = agreeing_types([a, b])
    assert classify(a, oracle_independent=True, agreeing=agreeing, now=NOW,
                    current_version="1") is Status.BELIEVED


def test_two_types_from_a_single_source_are_not_believed() -> None:
    """Source-independence (ADR-0008): one source fabricating two evidence types may
    not self-corroborate. Type-diversity alone is not independence."""
    a = summary(SignalType.BEHAVIORAL, source="agent-1")
    b = summary(SignalType.NETWORK, source="agent-1")
    assert oracle_believed([a, b]) is False
    agreeing = agreeing_types([a, b])
    assert classify(a, oracle_independent=False, agreeing=agreeing, now=NOW,
                    current_version="1") is Status.CONTESTED


def test_two_observations_of_same_type_do_not_create_diversity() -> None:
    a = summary(SignalType.NETWORK, present=(True, True), source="agent-1")
    b = summary(SignalType.NETWORK, present=(True, True, True), source="agent-2")
    assert oracle_believed([a, b]) is False  # same type → not independent


def test_seeded_oracle_is_believed_from_cold_start() -> None:
    """A human/spec seed is trusted on run one, exactly when an oracle is most
    needed — without self-certification by an exploring agent."""
    seed = summary(SignalType.BEHAVIORAL, seeded=True)
    assert oracle_believed([seed]) is True
    assert classify(seed, oracle_independent=True, agreeing={SignalType.BEHAVIORAL},
                    now=NOW, current_version="1") is Status.BELIEVED


# --- ADR-0029: per-signal promotion, no borrowing the goal-level flag --------

def test_lone_agent_summary_does_not_borrow_goal_level_flag() -> None:
    """ADR-0029 defect B: a lone single-agent success summary whose type a SEED
    already covers must NOT be promoted just because the GOAL has a seeded
    independent-diverse oracle (oracle_independent=True). With the per-signal
    `peers` set supplied, the lone same-type summary has no different-type partner
    from a different source, so it stays contested."""
    seed = summary(SignalType.BEHAVIORAL, seeded=True, source="spec")
    lone = summary(SignalType.BEHAVIORAL, source="agent-9")
    lone.value = "behavioral paraphrase signal"  # distinct value, same type as seed
    agreeing = agreeing_types([seed, lone])
    # The goal-level flag is True (the seed alone seeds belief), but the lone
    # agent summary itself is not corroborated, so it must stay contested.
    assert classify(lone, oracle_independent=True, agreeing=agreeing, now=NOW,
                    current_version="1", peers=[seed, lone]) is Status.CONTESTED
    # The seed itself stays believed (seeded arm), not over-corrected.
    assert classify(seed, oracle_independent=True, agreeing=agreeing, now=NOW,
                    current_version="1", peers=[seed, lone]) is Status.BELIEVED


def test_per_signal_keeps_inherent_seed_plus_different_type_agent() -> None:
    """ADR-0029 must NOT break the ADR-0008 INHERENT boundary: a seed of one type
    plus a SINGLE agent of a DIFFERENT type promotes the agent summary, because the
    agent summary's different-type partner (the seed) comes from a second distinct
    source. One genuine corroborating observation on a seed stays believed."""
    seed = summary(SignalType.BEHAVIORAL, seeded=True, source="AC-1")
    agent = summary(SignalType.NETWORK, source="agent-1")
    agreeing = agreeing_types([seed, agent])
    assert classify(agent, oracle_independent=True, agreeing=agreeing, now=NOW,
                    current_version="1", peers=[seed, agent]) is Status.BELIEVED


def test_summary_corroborated_helper_distinguishes_partner_from_self() -> None:
    a = summary(SignalType.BEHAVIORAL, source="agent-1")
    b = summary(SignalType.NETWORK, source="agent-2")
    # a has a different-type partner (b) from a different source -> corroborated.
    assert summary_corroborated(a, [a, b]) is True
    # A same-type-only stream gives no different-type partner -> not corroborated.
    c = summary(SignalType.BEHAVIORAL, source="agent-3")
    c.value = "another behavioral paraphrase"
    assert summary_corroborated(a, [a, c]) is False
    # Two types but a SINGLE shared source -> union is one source -> not corroborated.
    d = summary(SignalType.NETWORK, source="agent-1")
    assert summary_corroborated(a, [a, d]) is False


# --- quarantine + contradiction ---------------------------------------------

def test_flip_flop_is_quarantined() -> None:
    flip = summary(SignalType.BEHAVIORAL, present=(True, False, True))
    assert is_flip_flop(flip.presence) is True
    assert classify(flip, oracle_independent=True, agreeing={SignalType.BEHAVIORAL},
                    now=NOW, current_version="1") is Status.QUARANTINED


def test_single_disagreement_is_contested_not_quarantined() -> None:
    contra = summary(SignalType.BEHAVIORAL, present=(True, False))
    assert is_flip_flop(contra.presence) is False
    assert has_contradiction(contra.presence) is True
    assert classify(contra, oracle_independent=True, agreeing=set(),
                    now=NOW, current_version="1") is Status.CONTESTED


def test_quarantined_signal_does_not_count_toward_diversity() -> None:
    flip = summary(SignalType.BEHAVIORAL, present=(True, False, True))
    ok = summary(SignalType.NETWORK)
    # Only the network type is stable → not >=2 stable types → oracle not believed.
    assert agreeing_types([flip, ok]) == {SignalType.NETWORK}
    assert oracle_believed([flip, ok]) is False


# --- staleness + confidence -------------------------------------------------

def test_old_or_off_version_signal_is_stale() -> None:
    old = summary(SignalType.BEHAVIORAL, seeded=True, last=NOW - timedelta(days=200))
    assert classify(old, oracle_independent=True, agreeing={SignalType.BEHAVIORAL},
                    now=NOW, current_version="1") is Status.STALE
    offver = summary(SignalType.BEHAVIORAL, seeded=True, ver="0.9")
    assert classify(offver, oracle_independent=True, agreeing={SignalType.BEHAVIORAL},
                    now=NOW, current_version="2.0") is Status.STALE


def test_confidence_rises_with_count_and_decays_with_age() -> None:
    one = summary(SignalType.NETWORK, present=(True,))
    three = summary(SignalType.NETWORK, present=(True, True, True))
    assert confidence_of(three, now=NOW) > confidence_of(one, now=NOW)
    aged = summary(SignalType.NETWORK, present=(True, True, True),
                   last=NOW - timedelta(days=30))
    assert confidence_of(aged, now=NOW) < confidence_of(three, now=NOW)


def test_seed_confidence_floor() -> None:
    seed = summary(SignalType.BEHAVIORAL, seeded=True)
    assert confidence_of(seed, now=NOW) >= 0.9
