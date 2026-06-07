"""Merge folds events into the believed state, NEVER last-write-wins. Contradictions
are preserved as `contested`; oscillation becomes `quarantined`; seeded oracles work
from cold start."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mneme.merge import project, project_with_seed
from mneme.model import (
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    Target,
)
from mneme.store import ObservationEvent, ObservedSignal

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def ev(value, type_, present=True, ts=NOW, src_type="agent", src_id="a1",
       kind="success", ver="1") -> ObservationEvent:
    return ObservationEvent(
        agent_id=src_id, goal_id="g", ts=ts, observed_app_version=ver,
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


def test_seed_plus_agent_observation_merges() -> None:
    kf = project_with_seed(
        _seed(), [ev("POST /session 2xx", "network")], now=NOW, current_version="1")
    values = {s.value for s in kf.success_signals}
    assert "POST /session 2xx" in values
    assert "authenticated home reachable (AC)" in values
