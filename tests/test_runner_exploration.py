"""E-mode (exploration) runner tests.

Cover: candidate observations flow into the store as `contested`; agent-emitted
risks are forced to `contested` regardless of executor claim (ADR-0008
single-source-can-not-self-promote); off_path_fraction computation; the
agent_id is used as source_id on emitted observations so multi-run repeats
do not bootstrap independence.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from praxis.adapters import BrowserUseAdapter
from praxis.model import (
    KnowledgeFile,
    Meta,
    Provenance,
    Risk,
    SequenceTrigger,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
    Uncertainty,
)
from praxis.runner import (
    ExplorationRunner,
    compute_off_path_fraction,
)
from praxis.store import FileEventStore, ObservedSignal


def _provenance(source_type: SourceType = SourceType.HUMAN,
                source_id: str = "spec-1") -> Provenance:
    return Provenance(
        source_type=source_type, source_id=source_id,
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )


def _kf_with_risks() -> KnowledgeFile:
    return KnowledgeFile(
        schema_version="0",
        goal_id="checkout",
        goal="a user can purchase items",
        target=Target(app="testapp"),
        success_signals=[
            Signal(type=SignalType.NETWORK,
                   value="POST /orders returns 200 with order_id",
                   provenance=_provenance(), confidence=1.0, status=Status.BELIEVED),
        ],
        failure_signals=[
            Signal(type=SignalType.NETWORK,
                   value="duplicate order_id created on idempotent retry",
                   provenance=_provenance(), confidence=1.0, status=Status.BELIEVED),
        ],
        risks=[
            Risk(id="idempotency",
                 description="POST /orders with same Idempotency-Key creates two orders",
                 trigger=SequenceTrigger(n=2, action="submit checkout with same Idempotency-Key",
                                          expect="two distinct order_ids returned"),
                 provenance=_provenance(), confidence=0.9, status=Status.BELIEVED),
        ],
        uncertainties=[
            Uncertainty(id="u-receipt-window",
                        question="how long is the receipt URL valid?",
                        raised_by="explorer-1",
                        raised_at=datetime(2026, 6, 1, tzinfo=timezone.utc)),
        ],
        meta=Meta(created_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
                  updated_at=datetime(2026, 6, 7, tzinfo=timezone.utc)),
    )


def _seeded_adapter(kf: KnowledgeFile, dirpath: Path) -> BrowserUseAdapter:
    store = FileEventStore(str(dirpath))
    return BrowserUseAdapter(store, target=kf.target, seeds={kf.goal_id: kf})


# --- off_path_fraction ------------------------------------------------------


def test_off_path_fraction_zero_when_no_visits() -> None:
    assert compute_off_path_fraction([], ["/login"]) == 0.0


def test_off_path_fraction_one_when_no_happy_path() -> None:
    # Cold-discovery case: no happy path known, everything is off-path by def.
    assert compute_off_path_fraction(["/x", "/y"], []) == 1.0


def test_off_path_fraction_typical() -> None:
    visited = ["/login", "/cart", "/cart/coupon", "/cart/coupon", "/admin"]
    happy = ["/login", "/cart"]
    # 3 of 5 visits are off-path (/cart/coupon x2 and /admin).
    assert compute_off_path_fraction(visited, happy) == pytest.approx(0.6)


def test_off_path_fraction_normalizes_trailing_slash() -> None:
    visited = ["/login/", "/cart"]
    happy = ["/login", "/cart/"]
    assert compute_off_path_fraction(visited, happy) == 0.0


# --- runner -----------------------------------------------------------------


def test_exploration_emits_candidates_as_contested_observations() -> None:
    kf = _kf_with_risks()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))

        def executor(prompt: str) -> dict:
            assert "EXPLORATION" in prompt
            assert "SEQUENCE 2x submit checkout" in prompt
            return {
                "candidate_observations": [
                    ObservedSignal(
                        kind="failure", type=SignalType.NETWORK,
                        value="duplicate order_id created on idempotent retry",
                        source_type=SourceType.AGENT, source_id="praxis-explore",
                    ),
                ],
                "new_risks": [],
                "new_uncertainties": [],
                "actions": 7, "tokens": 4200,
                "visited_urls": ["/cart", "/checkout", "/admin"],
            }

        runner = ExplorationRunner(adapter)
        result = runner.run_one(
            "checkout", executor,
            happy_path_urls=["/cart", "/checkout"],
            budget_actions=10,
        )
        assert len(result.candidate_observations) == 1
        # Visited [cart, checkout, admin]; happy = [cart, checkout] -> 1/3 off.
        assert result.off_path_fraction == pytest.approx(1 / 3)
        # Observation persisted into the store with agent_id as source_id (the
        # ADR-0008 anchor: same source over many runs is not independence).
        events = list(adapter.store.read("checkout"))
        assert len(events) == 1
        assert events[0].agent_id == "praxis-explore"
        assert events[0].signals[0].source_id == "praxis-explore"


def test_exploration_forces_new_risks_to_contested() -> None:
    """An executor might claim a new risk is `believed`; the runner refuses."""
    kf = _kf_with_risks()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))

        believed_provenance = _provenance(source_type=SourceType.AGENT,
                                          source_id="praxis-explore").model_dump(mode="json")

        def executor(prompt: str) -> dict:
            return {
                "candidate_observations": [],
                "new_risks": [{
                    "id": "newly-found",
                    "description": "agent thinks this is broken",
                    "trigger": {"kind": "http", "method": "GET", "path": "/x",
                                 "expect": "200"},
                    # Executor falsely claims BELIEVED:
                    "status": "believed",
                    "confidence": 0.99,
                    "provenance": believed_provenance,
                }],
                "new_uncertainties": [],
                "actions": 1, "tokens": 100,
                "visited_urls": [],
            }

        runner = ExplorationRunner(adapter)
        result = runner.run_one("checkout", executor)
        assert len(result.new_risks) == 1
        # The runner forced contested - ADR-0008 source-independence invariant.
        assert result.new_risks[0].status == Status.CONTESTED


def test_exploration_unknown_goal_raises() -> None:
    kf = _kf_with_risks()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = ExplorationRunner(adapter)
        with pytest.raises(ValueError):
            runner.run_one("nope", lambda _: {"candidate_observations": []})


def test_exploration_drops_new_risk_with_banned_expect_phrase() -> None:
    """A new risk whose `expect` predicate matches a banned phrase is
    rejected by the trigger validator (ADR-0009 sec 4) and surfaces in
    `notes`. It never enters `new_risks` and so never reaches the store."""
    kf = _kf_with_risks()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        prov = _provenance(source_type=SourceType.AGENT,
                           source_id="praxis-explore").model_dump(mode="json")

        def executor(prompt: str) -> dict:
            return {
                "candidate_observations": [],
                "new_risks": [{
                    "id": "vague-claim",
                    "description": "agent saw something flaky",
                    "trigger": {"kind": "http", "method": "GET", "path": "/x",
                                 "expect": "fails under high load"},
                    "status": "contested",
                    "confidence": 0.5,
                    "provenance": prov,
                }],
                "new_uncertainties": [],
                "actions": 1, "tokens": 100,
                "visited_urls": [],
            }

        runner = ExplorationRunner(adapter)
        result = runner.run_one("checkout", executor)
        assert result.new_risks == []
        assert any("REJECTED" in n and "vague-claim" in n
                   for n in result.notes)


def test_exploration_persists_accepted_new_risks_as_candidate_events() -> None:
    """ADR-0014: agent-proposed new risks become durable `CandidateEvent`s
    via the adapter's `write_candidates` path. The runner forces
    `agent_identity = self.agent_id`, never `run_uuid` (ADR-0008)."""
    kf = _kf_with_risks()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        prov = _provenance(source_type=SourceType.AGENT,
                            source_id="should-be-overwritten").model_dump(mode="json")

        def executor(prompt: str) -> dict:
            return {
                "candidate_observations": [],
                "new_risks": [{
                    "id": "newly-found",
                    "description": "an unobserved failure mode",
                    "trigger": {"kind": "http", "method": "GET", "path": "/x",
                                 "expect": "Location header equals /home"},
                    "status": "contested", "confidence": 0.6,
                    "provenance": prov,
                }],
                "new_uncertainties": [{
                    "id": "u-receipt-window",
                    "question": "how long is the receipt URL valid?",
                    "raised_by": "ignored-by-adapter",
                    "raised_at": "2026-06-07T00:00:00Z",
                }],
                "actions": 2, "tokens": 200, "visited_urls": [],
            }

        runner = ExplorationRunner(adapter, agent_id="praxis-explore")
        runner.run_one("checkout", executor)
        cands = adapter.store.read_candidates("checkout")
        # One risk + one uncertainty -> two CandidateEvents.
        assert len(cands) == 2
        # The adapter forced `agent_identity` on every event.
        assert {ev.agent_identity for ev in cands} == {"praxis-explore"}
        # The risk event also has provenance.source_id forced.
        risk_ev = next(ev for ev in cands if ev.candidate_kind == "candidate_risk")
        assert risk_ev.provenance is not None
        assert risk_ev.provenance.source_id == "praxis-explore"


def test_exploration_does_not_persist_when_disabled() -> None:
    kf = _kf_with_risks()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))

        def executor(_: str) -> dict:
            return {
                "candidate_observations": [
                    ObservedSignal(
                        kind="failure", type=SignalType.NETWORK,
                        value="x", source_type=SourceType.AGENT, source_id="x",
                    ),
                ],
                "actions": 1, "tokens": 0, "visited_urls": [],
            }

        runner = ExplorationRunner(adapter)
        runner.run_one("checkout", executor, persist_observations=False)
        assert list(adapter.store.read("checkout")) == []


def test_explore_candidate_success_observation_stays_contested_until_corroborated() -> None:
    """ADR-0014 + ADR-0008 + ADR-0029: an E-mode candidate observation is WRITTEN to
    the promotable store (that is E-mode's job, unlike R-mode regress) but it enters
    as `contested` and is promoted ONLY by genuine independent-diverse corroboration,
    never by a single explorer self-certifying.

    The new candidate here is of the SAME type as the goal's only seeded success
    signal (network), so it has NO different-type partner to ride: one explorer
    observing it once leaves it contested. A SECOND explorer observing a
    DIFFERENT-type (behavioral) signal of the same claim is genuine 2-source /
    2-type evidence and promotes it (the positive control, not the inherent
    seed-rides-single-agent case which needs a DIFFERENT type from the seed)."""
    kf = _kf_with_risks()  # the only seeded success signal is NETWORK
    new_value = "GET /orders/{id} returns the persisted order after checkout"
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))

        def explorer_one(_: str) -> dict:
            return {
                "candidate_observations": [
                    ObservedSignal(
                        kind="success", type=SignalType.NETWORK, value=new_value,
                        source_type=SourceType.AGENT, source_id="praxis-explore",
                    ),
                ],
                "actions": 1, "tokens": 0, "visited_urls": [],
            }

        runner = ExplorationRunner(adapter)
        runner.run_one("checkout", explorer_one, budget_actions=10)

        # The candidate WAS persisted (E-mode writes), but a single explorer with no
        # different-type partner cannot self-promote: the new value reads as
        # contested, never believed.
        statuses = {
            s.value: s.status for s in adapter.read_knowledge("checkout").success_signals  # type: ignore[union-attr]
        }
        assert statuses[new_value] == Status.CONTESTED

        # A second, DISTINCT explorer observes a DIFFERENT-type signal for the same
        # new claim -> genuine 2-source / 2-type corroboration promotes it (ADR-0008).
        adapter.write_observations(
            goal_id="checkout", agent_id="praxis-explore-2",
            observations=[ObservedSignal(
                kind="success", type=SignalType.BEHAVIORAL, value=new_value,
                source_type=SourceType.AGENT, source_id="praxis-explore-2",
            )],
        )
        promoted = [
            s.status for s in adapter.read_knowledge("checkout").success_signals  # type: ignore[union-attr]
            if s.value == new_value
        ]
        assert promoted and all(st == Status.BELIEVED for st in promoted)
