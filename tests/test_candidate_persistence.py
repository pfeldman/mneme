"""Phase 2 (ADR-0014) tests: CandidateEvent persistence + projection.

The four required scenarios from the brief:
  1. contested-by-default-on-single-source
  2. promotion-on-diverse-second-source
  3. no-promotion-on-same-agent-identity-second-write
  4. schema-validator-rejects-banned-phrases-on-trigger

Plus auxiliary checks on the sibling event shape (own schema_version, store
separation, redaction, immutability).
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from praxis.adapters import BrowserUseAdapter, CandidateRejected
from praxis.merge import contested_candidates, project_candidates
from praxis.model import (
    HttpTrigger,
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
from praxis.store import (
    CandidateEvent,
    CandidateRiskPayload,
    FileEventStore,
    ObservationEvent,
    ObservedSignal,
)


# --- helpers ----------------------------------------------------------------


def _provenance(
    source_type: SourceType = SourceType.AGENT,
    source_id: str = "agent-A",
) -> Provenance:
    return Provenance(
        source_type=source_type, source_id=source_id,
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )


def _risk(
    id_: str = "idempotency",
    description: str = "POST /orders with same Idempotency-Key creates two orders",
    source_id: str = "agent-A",
    source_type: SourceType = SourceType.AGENT,
    trigger_kind: str = "sequence",
) -> Risk:
    trigger: SequenceTrigger | HttpTrigger
    if trigger_kind == "http":
        trigger = HttpTrigger(
            method="POST", path="/orders",
            expect="returns 200 with same order_id",
        )
    else:
        trigger = SequenceTrigger(
            n=2, action="submit checkout with same Idempotency-Key",
            expect="two distinct order_ids returned",
        )
    return Risk(
        id=id_, description=description, trigger=trigger,
        provenance=_provenance(source_type=source_type, source_id=source_id),
        confidence=0.7, status=Status.CONTESTED,
    )


def _uncertainty(
    id_: str = "receipt-window",
    raised_by: str = "agent-A",
    question: str = "how long is the receipt URL valid?",
) -> Uncertainty:
    return Uncertainty(
        id=id_, question=question, raised_by=raised_by,
        raised_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def _seeded_kf() -> KnowledgeFile:
    """Minimal seeded KnowledgeFile (no candidate risk yet)."""
    return KnowledgeFile(
        schema_version="0",
        goal_id="checkout",
        goal="a user can purchase items",
        target=Target(app="testapp"),
        success_signals=[
            Signal(type=SignalType.NETWORK,
                   value="POST /orders returns 200 with order_id",
                   provenance=_provenance(
                       source_type=SourceType.HUMAN, source_id="spec-1"),
                   confidence=1.0, status=Status.BELIEVED),
        ],
        meta=Meta(created_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
                  updated_at=datetime(2026, 6, 7, tzinfo=timezone.utc)),
    )


def _adapter(tmp: Path, kf: KnowledgeFile | None = None) -> BrowserUseAdapter:
    store = FileEventStore(str(tmp))
    seeds = {kf.goal_id: kf} if kf else {}
    target = kf.target if kf else Target(app="testapp")
    return BrowserUseAdapter(store, target=target, seeds=seeds)


# --- 1. contested-by-default-on-single-source ------------------------------


def test_candidate_event_is_contested_with_single_source() -> None:
    """One CandidateEvent from one agent_identity, no seed: status=contested."""
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=_seeded_kf())
        ids = adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[_risk(source_id="agent-A")],
        )
        assert len(ids) == 1
        events = adapter.store.read_candidates("checkout")
        assert len(events) == 1
        projected = project_candidates(events, goal_id="checkout")
        assert len(projected) == 1
        assert projected[0].status == Status.CONTESTED
        # Author of the candidate is the only source: independence not satisfied.
        assert projected[0].distinct_source_ids == {"agent-A"}


def test_candidate_event_is_contested_even_when_executor_claimed_believed() -> None:
    """The adapter never trusts a `believed` claim on a candidate write.

    The runner re-writes status, but the projection is what `praxis review`
    reads. Single source -> contested regardless of payload.status.
    """
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=_seeded_kf())
        r = _risk()
        r.status = Status.BELIEVED  # simulate executor lying
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A", new_risks=[r],
        )
        events = adapter.store.read_candidates("checkout")
        projected = project_candidates(events, goal_id="checkout")
        # The projection ignores payload.status; it computes from sources.
        assert projected[0].status == Status.CONTESTED


# --- 2. promotion-on-diverse-second-source ---------------------------------


def test_candidate_promotes_with_seed_match_as_second_source() -> None:
    """Seed Risk with same id + candidate Risk = two independent source_ids.

    This is the canonical ADR-0014 sec 4 promotion path: a reviewer
    promotes by adding a NEW seed event (the candidate is never edited).
    Same trigger.kind from seed + candidate satisfies the diversity rule
    because seed source_type=human is independent from agent.
    """
    seed = _seeded_kf().model_copy(update={
        "risks": [
            # Seed risk WITH http trigger.
            Risk(
                id="idempotency", description="seeded risk",
                trigger=HttpTrigger(method="POST", path="/orders",
                                     expect="returns 200 with same order_id"),
                provenance=_provenance(source_type=SourceType.HUMAN,
                                       source_id="spec-1"),
                confidence=1.0, status=Status.BELIEVED,
            ),
        ],
    })
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=seed)
        # Candidate with a SEQUENCE trigger (different evidence kind).
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[_risk(source_id="agent-A", trigger_kind="sequence")],
        )
        events = adapter.store.read_candidates("checkout")
        projected = project_candidates(
            events, goal_id="checkout", seed=seed,
        )
        # Two distinct sources (spec-1 + agent-A) AND two distinct trigger
        # kinds (http + sequence): promotion satisfies independent_diverse.
        pc = next(p for p in projected if p.candidate_id == "idempotency")
        assert pc.distinct_source_ids == {"spec-1", "agent-A"}
        assert pc.distinct_evidence_kinds == {"http", "sequence"}
        assert pc.status == Status.BELIEVED


def test_candidate_promotes_with_two_diverse_agents_distinct_trigger_kinds() -> None:
    """Two DIFFERENT agent_identities write the same candidate id with
    DIFFERENT trigger.kind: diversity rule satisfied without a seed.

    This is the agent-only diverse-corroboration path; promotion requires
    BOTH source-independence AND type-diversity (ADR-0008).
    """
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=_seeded_kf())
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[_risk(source_id="agent-A", trigger_kind="sequence")],
        )
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-B",
            new_risks=[_risk(source_id="agent-B", trigger_kind="http")],
        )
        events = adapter.store.read_candidates("checkout")
        projected = project_candidates(events, goal_id="checkout")
        pc = projected[0]
        assert pc.distinct_source_ids == {"agent-A", "agent-B"}
        assert pc.distinct_evidence_kinds == {"sequence", "http"}
        assert pc.status == Status.BELIEVED


# --- 3. no-promotion-on-same-agent-identity-second-write -------------------


def test_no_promotion_on_same_agent_identity_second_write() -> None:
    """Two writes by the SAME agent_identity for the same candidate id do NOT
    self-promote (ADR-0008 source-independence + ADR-0014 sec 2).

    Even if the second write uses a different trigger.kind, source_id =
    agent_identity collapses both writes to one source under the rule.
    """
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=_seeded_kf())
        # Same agent_identity writes the same candidate id twice, with
        # DIFFERENT trigger kinds (so type-diversity is satisfied).
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[_risk(source_id="agent-A", trigger_kind="sequence")],
        )
        # The runner's force will overwrite the executor-supplied source_id
        # back to agent_identity anyway; we test that effect here too.
        risk_b = _risk(source_id="should-be-overwritten", trigger_kind="http")
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[risk_b],
        )
        events = adapter.store.read_candidates("checkout")
        assert len(events) == 2
        # The adapter forced source_id = agent_identity on BOTH writes.
        for ev in events:
            assert isinstance(ev.payload, CandidateRiskPayload)
            assert ev.payload.risk.provenance.source_id == "agent-A"
        projected = project_candidates(events, goal_id="checkout")
        pc = projected[0]
        # One source under the rule, so still contested.
        assert pc.distinct_source_ids == {"agent-A"}
        assert pc.status == Status.CONTESTED


# --- 4. schema-validator-rejects-banned-phrases-on-trigger -----------------


def test_adapter_rejects_banned_phrase_on_trigger_strict() -> None:
    """A candidate risk whose `expect` matches a banned phrase is REJECTED
    at the adapter boundary (ADR-0014 sec 3 + ADR-0009 sec 4).

    In strict mode the adapter raises; nothing enters the store.
    """
    bad_risk = Risk(
        id="vague", description="bad risk",
        trigger=HttpTrigger(method="GET", path="/x",
                             expect="fails under high load"),
        provenance=_provenance(source_id="agent-A"),
        confidence=0.5, status=Status.CONTESTED,
    )
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=_seeded_kf())
        with pytest.raises(CandidateRejected):
            adapter.write_candidates(
                goal_id="checkout", agent_identity="agent-A",
                new_risks=[bad_risk], strict_rejections=True,
            )
        # Even after a rejection, the store should be empty.
        assert adapter.store.read_candidates("checkout") == []


def test_adapter_drops_banned_phrase_on_trigger_default_mode() -> None:
    """Default (non-strict) mode silently drops rejected risks; the runner
    surfaces them in `notes` (covered by test_runner_exploration)."""
    bad_risk = Risk(
        id="vague", description="x",
        trigger=HttpTrigger(method="GET", path="/x", expect="flaky behaviour"),
        provenance=_provenance(source_id="agent-A"),
        confidence=0.5, status=Status.CONTESTED,
    )
    good_risk = _risk(id_="good", source_id="agent-A", trigger_kind="sequence")
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=_seeded_kf())
        ids = adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[bad_risk, good_risk],
        )
        # Only the good risk got persisted.
        assert len(ids) == 1
        events = adapter.store.read_candidates("checkout")
        assert len(events) == 1
        assert isinstance(events[0].payload, CandidateRiskPayload)
        assert events[0].payload.risk.id == "good"


# --- Sibling event shape (ADR-0014 sec 1) ----------------------------------


def test_candidate_event_has_own_schema_version_independent_of_observation() -> None:
    """CandidateEvent carries its own `schema_version` (the ADR-0014 sec 1
    point of the sibling type)."""
    risk = _risk()
    ev = CandidateEvent(
        agent_identity="agent-A", goal_id="checkout",
        payload=CandidateRiskPayload(risk=risk),
    )
    # The literal attribute exists and is part of the model.
    assert ev.schema_version == "0"
    obs = ObservationEvent(agent_id="agent-A", goal_id="checkout")
    assert obs.schema_version == "0"
    # They are independently typed Literals: changing one in the future
    # would not silently flip the other (sibling, not extension).
    assert "schema_version" in CandidateEvent.model_fields
    assert "schema_version" in ObservationEvent.model_fields
    assert (CandidateEvent.model_fields["schema_version"]
            is not ObservationEvent.model_fields["schema_version"])


def test_candidate_uncertainty_promotes_with_seed_match_only() -> None:
    """Uncertainties have no intrinsic evidence type; their diversity rule
    collapses to source diversity. Seed + candidate = promoted."""
    seed = _seeded_kf().model_copy(update={
        "uncertainties": [_uncertainty(id_="receipt-window", raised_by="spec-1")],
    })
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=seed)
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_uncertainties=[_uncertainty(id_="receipt-window")],
        )
        events = adapter.store.read_candidates("checkout")
        projected = project_candidates(
            events, goal_id="checkout", seed=seed,
        )
        pc = next(p for p in projected
                  if p.candidate_kind == "candidate_uncertainty")
        assert pc.distinct_source_ids == {"spec-1", "agent-A"}
        assert pc.status == Status.BELIEVED


def test_candidate_uncertainty_contested_with_single_source() -> None:
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=_seeded_kf())
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_uncertainties=[_uncertainty()],
        )
        events = adapter.store.read_candidates("checkout")
        projected = project_candidates(events, goal_id="checkout")
        assert projected[0].status == Status.CONTESTED


# --- Store separation (ADR-0014: read paths disjoint) ----------------------


def test_candidate_files_do_not_pollute_observation_event_reads() -> None:
    """Candidate events live in the same directory as signal events but
    are picked up by separate read paths; mixing them would let the
    diversity gate count candidate writes as evidence (ADR-0008)."""
    with tempfile.TemporaryDirectory() as td:
        store = FileEventStore(str(td))
        # One observation, one candidate, in the same dir.
        store.append(ObservationEvent(
            agent_id="agent-A", goal_id="checkout",
            signals=[ObservedSignal(
                kind="success", type=SignalType.NETWORK,
                value="POST /orders returns 200",
                source_type=SourceType.AGENT, source_id="agent-A",
            )],
        ))
        store.append_candidate(CandidateEvent(
            agent_identity="agent-A", goal_id="checkout",
            payload=CandidateRiskPayload(risk=_risk()),
        ))
        obs = store.read("checkout")
        cands = store.read_candidates("checkout")
        assert len(obs) == 1
        assert len(cands) == 1
        # The observation read path returns only signal events.
        assert isinstance(obs[0], ObservationEvent)
        # The candidate read path returns only candidate events.
        assert isinstance(cands[0], CandidateEvent)


def test_append_candidate_raises_on_duplicate_event_id(tmp_path: Path) -> None:
    """Append-only (ADR-0001) for candidates: re-appending the same id raises."""
    store = FileEventStore(str(tmp_path))
    ev = CandidateEvent(
        agent_identity="agent-A", goal_id="checkout",
        payload=CandidateRiskPayload(risk=_risk()),
    )
    store.append_candidate(ev)
    with pytest.raises(FileExistsError):
        store.append_candidate(ev)


# --- Promotion via human seed event (ADR-0014 sec 4) -----------------------


def test_review_surfaces_only_contested_candidates() -> None:
    """`contested_candidates` filters the projection to the queue
    `praxis review` should show. Believed (promoted) candidates do not
    re-appear in the queue."""
    seed = _seeded_kf().model_copy(update={
        "risks": [
            Risk(
                id="idempotency", description="seeded risk",
                trigger=HttpTrigger(method="POST", path="/orders",
                                     expect="returns 200 with same order_id"),
                provenance=_provenance(source_type=SourceType.HUMAN,
                                       source_id="spec-1"),
                confidence=1.0, status=Status.BELIEVED,
            ),
        ],
    })
    with tempfile.TemporaryDirectory() as td:
        adapter = _adapter(Path(td), kf=seed)
        # One candidate that matches the seeded risk id -> promotes.
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[_risk(id_="idempotency", source_id="agent-A",
                             trigger_kind="sequence")],
        )
        # One candidate with no seed match -> stays contested.
        adapter.write_candidates(
            goal_id="checkout", agent_identity="agent-A",
            new_risks=[_risk(id_="unseen-risk", source_id="agent-A",
                             trigger_kind="sequence")],
        )
        events = adapter.store.read_candidates("checkout")
        projected = project_candidates(
            events, goal_id="checkout", seed=seed,
        )
        queue = contested_candidates(projected)
        ids_in_queue = [pc.candidate_id for pc in queue]
        assert ids_in_queue == ["unseen-risk"]
