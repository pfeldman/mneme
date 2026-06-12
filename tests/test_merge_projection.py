"""Merge folds events into the believed state, NEVER last-write-wins. Contradictions
are preserved as `contested`; oscillation becomes `quarantined`; seeded oracles work
from cold start."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from praxis.merge import project, project_with_seed
from praxis.model import (
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    Target,
)
from praxis.store import ObservationEvent, ObservedSignal

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def ev(value, type_, present=True, ts=NOW, src_type="agent", src_id="a1",
       kind="success", ver="1", env=None) -> ObservationEvent:
    return ObservationEvent(
        agent_id=src_id, goal_id="g", ts=ts, observed_app_version=ver,
        environment=env,
        signals=[ObservedSignal(kind=kind, type=type_, value=value, present=present,
                                source_type=src_type, source_id=src_id,
                                observed_app_version=ver)],
    )


def proj(events, **kw):
    return project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                   now=NOW, current_version="1", **kw)


def test_repeated_same_type_is_aggregated_not_duplicated() -> None:
    kf = proj([ev("logout", "behavioral", ts=NOW - timedelta(days=2)),
               ev("logout", "behavioral", ts=NOW - timedelta(days=1)),
               ev("logout", "behavioral", ts=NOW)])
    assert len(kf.success_signals) == 1
    assert kf.success_signals[0].provenance.observation_count == 3


def test_diversity_promotes_to_believed() -> None:
    # Two types from two DISTINCT sources → type-diverse AND source-independent.
    kf = proj([ev("logout", "behavioral", src_id="a1"),
               ev("POST /session 2xx", "network", src_id="a2")])
    assert {s.status.value for s in kf.success_signals} == {"believed"}


def test_two_types_from_one_source_are_not_believed() -> None:
    # Source-independence (ADR-0008): a single source cannot self-corroborate across
    # types — both stay contested, never believed.
    kf = proj([ev("logout", "behavioral", src_id="a1"),
               ev("POST /session 2xx", "network", src_id="a1")])
    assert {s.status.value for s in kf.success_signals} == {"contested"}


def test_contradiction_is_preserved_as_contested_not_resolved() -> None:
    # Same signal seen, then explicitly NOT seen → disagreement kept, not collapsed.
    # (A success signal is included so the projection is valid; the contradiction is
    # on the failure signal.)
    kf = proj([ev("logout", "behavioral"),
               ev("captcha appears", "behavioral", present=True, kind="failure",
                  ts=NOW - timedelta(days=1)),
               ev("captcha appears", "behavioral", present=False, kind="failure", ts=NOW)])
    assert kf.failure_signals is not None
    captcha = kf.failure_signals[0]
    assert captcha.status.value == "contested"


def test_oscillation_is_quarantined() -> None:
    kf = proj([ev("flaky", "behavioral", present=True, ts=NOW - timedelta(days=2)),
               ev("flaky", "behavioral", present=False, ts=NOW - timedelta(days=1)),
               ev("flaky", "behavioral", present=True, ts=NOW)])
    assert kf.success_signals[0].status.value == "quarantined"


def test_never_last_write_wins() -> None:
    # The most recent event says "not present", but the earlier positives are NOT
    # erased — both are represented (here as a contested signal).
    kf = proj([ev("x", "behavioral", present=True, ts=NOW - timedelta(days=1)),
               ev("x", "behavioral", present=False, ts=NOW)])
    s = kf.success_signals[0]
    assert s.status.value == "contested"
    assert s.provenance.observation_count == 2  # both observations retained


def test_projection_is_deterministic() -> None:
    events = [ev("logout", "behavioral"), ev("POST /session 2xx", "network")]
    assert proj(events) == proj(list(reversed(events)))  # order-independent


def test_project_without_success_signal_raises() -> None:
    with pytest.raises(ValueError):
        proj([ev("only a failure", "text", kind="failure")])


def _seed() -> KnowledgeFile:
    return KnowledgeFile(
        schema_version="0", goal_id="g", goal="auth",
        target=Target(app="acme", observed_app_versions=["1"]),
        success_signals=[Signal(
            type="behavioral", value="authenticated home reachable (AC)",
            provenance=Provenance(source_type="spec", source_id="AC-1",
                                  observed_app_version="1", last_verified=NOW,
                                  observation_count=1),
            confidence=1.0, status="believed")],
        meta=Meta(created_at=NOW, updated_at=NOW),
    )


def test_seed_makes_oracle_believed_from_cold_start() -> None:
    kf = project_with_seed(_seed(), [], now=NOW, current_version="1")
    assert any(s.status.value == "believed" for s in kf.success_signals)


def test_value_predicate_survives_the_projection() -> None:
    """ADR-0030 read-path: a seed-authored `value_predicate` must survive the
    observation->believed rebuild, or the matcher silently falls back to Jaccard
    and the structured fact path is dead end to end (the live-proof gap). The
    projection restores it from the seed by (type, value)."""
    seed = _seed()
    seed.success_signals[0] = Signal(
        type="behavioral", value="authenticated home reachable (AC)",
        value_predicate="authenticated home reachable for {user}",
        provenance=Provenance(source_type="spec", source_id="AC-1",
                              observed_app_version="1", last_verified=NOW,
                              observation_count=1),
        confidence=1.0, status="believed")
    kf = project_with_seed(seed, [], now=NOW, current_version="1")
    match = [s for s in kf.success_signals
             if s.value == "authenticated home reachable (AC)"]
    assert match and match[0].value_predicate == "authenticated home reachable for {user}"


def test_check_survives_the_projection() -> None:
    """ADR-0031 read-path: a seed-authored structured `check` must survive the
    observation->believed rebuild, or the matcher silently falls back to a looser
    path and the structured fact is dead end to end. The projection restores it
    from the seed by (type, value), alongside `value_predicate`."""
    from praxis.model import ListCountDeltaCheck

    seed = _seed()
    seed.success_signals[0] = Signal(
        type="behavioral", value="authenticated home reachable (AC)",
        check=ListCountDeltaCheck(expect_delta=-1),
        provenance=Provenance(source_type="spec", source_id="AC-1",
                              observed_app_version="1", last_verified=NOW,
                              observation_count=1),
        confidence=1.0, status="believed")
    kf = project_with_seed(seed, [], now=NOW, current_version="1")
    match = [s for s in kf.success_signals
             if s.value == "authenticated home reachable (AC)"]
    assert match and match[0].check == ListCountDeltaCheck(expect_delta=-1)


def test_seed_plus_agent_observation_merges() -> None:
    kf = project_with_seed(
        _seed(), [ev("POST /session 2xx", "network")], now=NOW, current_version="1")
    values = {s.value for s in kf.success_signals}
    assert "POST /session 2xx" in values
    assert "authenticated home reachable (AC)" in values


def test_seed_plus_same_type_paraphrase_stream_believes_only_the_seed() -> None:
    """ADR-0029 defect B at the projection level: a SEED (behavioral) plus a STREAM
    of distinct single-agent paraphrases of the SAME type must NOT inflate the
    believed set. Each paraphrase rode the goal-level independence flag under the
    bug; with the per-signal rule none has a different-type partner from a different
    source, so the only believed success signal is the SEED."""
    seed_value = "authenticated home reachable (AC)"  # behavioral, from _seed()
    stream = [
        ev(f"a behavioral paraphrase #{i} of the success", "behavioral", src_id=f"a{i}")
        for i in range(26)
    ]
    kf = project_with_seed(_seed(), stream, now=NOW, current_version="1")
    believed = {s.value for s in kf.success_signals if s.status.value == "believed"}
    assert believed == {seed_value}
    # The paraphrases are present in the projection (nothing dropped) but contested,
    # so they are loud and traceable, never silently promoted.
    contested = {s.value for s in kf.success_signals if s.status.value == "contested"}
    assert len(contested) == 26


def test_projection_is_environment_blind_env_never_mints_source_diversity() -> None:
    """ADR-0035 decision 5: `environment` is a field the core never interprets.
    Two different-type observations from ONE agent, stamped dev2 and prod and
    folded into ONE projection, still count as ONE source -> contested, never
    believed - and the projected provenance carries the bare agent identity,
    never an env-decorated source_id (`agent@dev2` is the forbidden ADR-0008
    breach). The partition itself lives at the adapter boundary; this pins
    that even an unpartitioned fold mints no diversity from the env field."""
    kf = proj([ev("logout", "behavioral", src_id="a1", env="dev2"),
               ev("POST /session 2xx", "network", src_id="a1", env="prod")])
    assert {s.status.value for s in kf.success_signals} == {"contested"}
    assert {s.provenance.source_id for s in kf.success_signals} == {"a1"}


def test_seed_plus_single_different_type_agent_promotes_the_agent() -> None:
    """ADR-0029 must preserve the ADR-0008 INHERENT boundary at the projection
    level: a behavioral SEED plus a SINGLE network agent observation promotes the
    network signal to believed (genuine different-type, different-source
    corroboration), so the believed set is BOTH."""
    kf = project_with_seed(
        _seed(), [ev("POST /session 2xx", "network", src_id="a1")],
        now=NOW, current_version="1")
    believed = {s.value for s in kf.success_signals if s.status.value == "believed"}
    assert believed == {"authenticated home reachable (AC)", "POST /session 2xx"}
