"""Phase 3 (ADR-0021 decision 4): the committed candidate tree.

`CandidateFileStore` persists each explore candidate as its own committed YAML
file under `.praxis/candidates/<goal>/<observation_event_id>.yaml`, one file per
observation, named by the content-addressable event id (ADR-0012), never a
shared mutable list. The judgment that two observations are the same finding is
made at PROJECTION time by grouping on `trigger`
(`merge.candidates.project_candidates`, whose rules this step does NOT change),
never by a filename collision or by editing a file.

The required scenarios from the Wave 1 Step 4 brief:
  1. two candidate adds for one goal are two distinct files (merge-safe, no
     shared edited line);
  2. two observations of one finding are two files sharing one `trigger`, and
     are deduped ONLY at the projection;
  3. a concurrent two-writer scenario loses no candidate (DoD: store-touch
     concurrency test);
  4. N observations from the same agent_identity still count as ONE source
     (ADR-0008) at the projection.

Plus round-trip, append-only, aggregate-read, and path-escape guards.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from praxis.merge import project_candidates
from praxis.model import (
    HttpTrigger,
    Provenance,
    Risk,
    SequenceTrigger,
    SourceType,
    Status,
    Uncertainty,
)
from praxis.store import (
    CandidateEvent,
    CandidateFileStore,
    CandidateRiskPayload,
    CandidateUncertaintyPayload,
)


# --- helpers ----------------------------------------------------------------


def _provenance(source_id: str = "agent-A") -> Provenance:
    return Provenance(
        source_type=SourceType.AGENT,
        source_id=source_id,
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )


def _risk(
    id_: str = "idempotency",
    source_id: str = "agent-A",
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
        id=id_,
        description="POST /orders with same Idempotency-Key creates two orders",
        trigger=trigger,
        provenance=_provenance(source_id=source_id),
        confidence=0.7, status=Status.CONTESTED,
    )


def _risk_event(
    goal_id: str = "checkout",
    agent_identity: str = "agent-A",
    risk_id: str = "idempotency",
    trigger_kind: str = "sequence",
    ts: datetime | None = None,
) -> CandidateEvent:
    return CandidateEvent(
        ts=ts or datetime.now(timezone.utc),
        agent_identity=agent_identity,
        goal_id=goal_id,
        payload=CandidateRiskPayload(
            risk=_risk(id_=risk_id, source_id=agent_identity,
                       trigger_kind=trigger_kind),
        ),
    )


def _uncertainty_event(
    goal_id: str = "checkout",
    agent_identity: str = "agent-A",
    unc_id: str = "receipt-window",
) -> CandidateEvent:
    return CandidateEvent(
        agent_identity=agent_identity,
        goal_id=goal_id,
        payload=CandidateUncertaintyPayload(
            uncertainty=Uncertainty(
                id=unc_id, question="how long is the receipt URL valid?",
                raised_by=agent_identity,
                raised_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            ),
        ),
    )


# --- 1. two candidate adds for one goal are two distinct files --------------


def test_two_adds_for_one_goal_are_two_distinct_files(tmp_path: Path) -> None:
    """Two candidate adds for the same goal land in two files under one
    per-goal directory, each named by its OWN observation event id. There is
    no shared mutable list, so there is no line both writers edited - the
    git-level realization of the ADR-0012 file-per-event store (ADR-0021
    decision 4)."""
    store = CandidateFileStore(tmp_path / "candidates")
    ev_a = _risk_event(risk_id="idempotency")
    ev_b = _risk_event(risk_id="phishy-redirect", trigger_kind="http")
    p_a = store.write(ev_a)
    p_b = store.write(ev_b)

    goal_dir = tmp_path / "candidates" / "checkout"
    files = sorted(goal_dir.glob("*.yaml"))
    assert len(files) == 2
    # Each file is named by its observation event id, not the finding id.
    assert p_a.name == f"{ev_a.event_id}.yaml"
    assert p_b.name == f"{ev_b.event_id}.yaml"
    assert p_a != p_b
    # No file is a shared list: each holds exactly one event with one event id.
    reloaded = store.read("checkout")
    assert {e.event_id for e in reloaded} == {ev_a.event_id, ev_b.event_id}


def test_candidate_file_round_trips_the_event(tmp_path: Path) -> None:
    """A committed YAML file re-hydrates into the same CandidateEvent (event
    id, agent_identity, payload, trigger all preserved)."""
    store = CandidateFileStore(tmp_path / "candidates")
    ev = _risk_event(risk_id="idempotency")
    store.write(ev)
    [reloaded] = store.read("checkout")
    assert reloaded.event_id == ev.event_id
    assert reloaded.agent_identity == ev.agent_identity
    assert reloaded.candidate_id == ev.candidate_id
    assert isinstance(reloaded.payload, CandidateRiskPayload)
    assert reloaded.payload.risk.trigger.kind == "sequence"


# --- 2. two observations of one finding: two files, one trigger, deduped only
#        at projection ------------------------------------------------------


def test_two_observations_of_one_finding_are_two_files_one_trigger(
    tmp_path: Path,
) -> None:
    """Two observations of the SAME finding (same candidate id, same structured
    trigger) from two DIFFERENT agents are two distinct files sharing one
    trigger; they are NEVER merged into one file. Deduplication and
    corroboration happen ONLY at the projection (ADR-0021 decision 4)."""
    store = CandidateFileStore(tmp_path / "candidates")
    # Two different agents observe the same finding with the same trigger.kind.
    ev_a = _risk_event(agent_identity="agent-A", risk_id="idempotency",
                       trigger_kind="sequence")
    ev_b = _risk_event(agent_identity="agent-B", risk_id="idempotency",
                       trigger_kind="sequence")
    store.write(ev_a)
    store.write(ev_b)

    # On disk: two files, never collapsed into one.
    goal_dir = tmp_path / "candidates" / "checkout"
    assert len(sorted(goal_dir.glob("*.yaml"))) == 2

    events = store.read("checkout")
    assert len(events) == 2
    # Both share one structured trigger (the finding identity), but they are
    # two separate observation events.
    triggers = {
        e.payload.risk.trigger.kind
        for e in events
        if isinstance(e.payload, CandidateRiskPayload)
    }
    assert triggers == {"sequence"}

    # Dedup happens ONLY at the projection: two observation events of one
    # finding group into ONE projected candidate.
    projected = project_candidates(events, goal_id="checkout")
    assert len(projected) == 1
    pc = projected[0]
    assert pc.candidate_id == "idempotency"
    # Two observation events corroborate the one finding.
    assert len(pc.corroborating_events) == 2
    # Two distinct sources but ONE evidence kind (same trigger): the projection
    # holds it contested, never self-promoted on same-type repeats (ADR-0008).
    assert pc.distinct_source_ids == {"agent-A", "agent-B"}
    assert pc.distinct_evidence_kinds == {"sequence"}
    assert pc.status == Status.CONTESTED


# --- 3. concurrent two-writer scenario loses no candidate -------------------


def test_concurrent_two_writers_lose_no_candidate(tmp_path: Path) -> None:
    """Two writers adding candidates concurrently for the same goal lose
    nothing: every observation event id lands as its own file, no shared line
    is edited, and the read folds them all back (DoD store-touch concurrency
    test; ADR-0021 decision 4 + ADR-0012 file-per-event guarantee)."""
    store = CandidateFileStore(tmp_path / "candidates")
    n_per_writer = 25
    events_a = [
        _risk_event(agent_identity="agent-A", risk_id=f"a-finding-{i}")
        for i in range(n_per_writer)
    ]
    events_b = [
        _risk_event(agent_identity="agent-B", risk_id=f"b-finding-{i}")
        for i in range(n_per_writer)
    ]
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _write(events: list[CandidateEvent]) -> None:
        barrier.wait()  # maximize the overlap window
        try:
            for ev in events:
                store.write(ev)
        except BaseException as exc:  # noqa: BLE001 - surface any race failure
            errors.append(exc)

    t_a = threading.Thread(target=_write, args=(events_a,))
    t_b = threading.Thread(target=_write, args=(events_b,))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    assert not errors, f"concurrent writers raised: {errors}"
    reloaded = store.read("checkout")
    expected_ids = {ev.event_id for ev in (*events_a, *events_b)}
    got_ids = {ev.event_id for ev in reloaded}
    # No discovery is lost on concurrent contribution.
    assert got_ids == expected_ids
    assert len(got_ids) == 2 * n_per_writer
    # On disk: exactly one file per observation event id, no shared list.
    goal_dir = tmp_path / "candidates" / "checkout"
    assert len(sorted(goal_dir.glob("*.yaml"))) == 2 * n_per_writer


# --- 4. N observations from one agent_identity = ONE source at projection ---


def test_n_same_agent_identity_observations_count_as_one_source(
    tmp_path: Path,
) -> None:
    """N candidate files from the SAME agent_identity for one finding still
    count as ONE source at the projection (ADR-0008 source-independence), even
    with mixed trigger kinds. One file per id gives merge-safety; source_id =
    agent_identity keeps the safety from becoming a self-promotion path
    (ADR-0021 decision 4)."""
    store = CandidateFileStore(tmp_path / "candidates")
    # Same agent writes the same finding id many times, with BOTH trigger kinds
    # (so type-diversity alone could otherwise look satisfied).
    kinds = ["sequence", "http", "sequence", "http", "sequence"]
    for i, kind in enumerate(kinds):
        store.write(_risk_event(
            agent_identity="agent-A", risk_id="idempotency",
            trigger_kind=kind,
            ts=datetime(2026, 6, 7, 12, 0, i, tzinfo=timezone.utc),
        ))

    # On disk: N distinct files (one per observation event id).
    goal_dir = tmp_path / "candidates" / "checkout"
    assert len(sorted(goal_dir.glob("*.yaml"))) == len(kinds)

    events = store.read("checkout")
    assert len(events) == len(kinds)
    projected = project_candidates(events, goal_id="checkout")
    # All N observations group into ONE finding.
    assert len(projected) == 1
    pc = projected[0]
    # The whole point of ADR-0008 under multi-writer: N same-model writes are
    # ONE source, so promotion never fires no matter the trigger-kind spread.
    assert pc.distinct_source_ids == {"agent-A"}
    assert pc.status == Status.CONTESTED


# --- aggregate read across goals (what `praxis review` folds) ---------------


def test_aggregate_read_folds_every_goal(tmp_path: Path) -> None:
    """Reading with no goal id folds every goal's candidate subdirectory, the
    aggregate queue `praxis review` consumes (ADR-0021 decision 4)."""
    store = CandidateFileStore(tmp_path / "candidates")
    store.write(_risk_event(goal_id="checkout", risk_id="idempotency"))
    store.write(_risk_event(goal_id="login", risk_id="redirect",
                            trigger_kind="http"))
    store.write(_uncertainty_event(goal_id="login", unc_id="receipt-window"))

    everything = store.read()  # no goal id -> aggregate
    assert len(everything) == 3
    assert set(store.goals()) == {"checkout", "login"}
    # Per-goal read still scopes to one goal.
    assert len(store.read("login")) == 2
    assert len(store.read("checkout")) == 1


def test_read_is_empty_for_fresh_or_unknown_project(tmp_path: Path) -> None:
    """A candidates dir that does not exist yet (fresh project) reads empty,
    never raises; ditto an unknown goal id."""
    store = CandidateFileStore(tmp_path / "does-not-exist")
    assert store.read() == []
    assert store.read("nope") == []
    assert store.goals() == []


# --- append-only + path-escape guards --------------------------------------


def test_rewriting_same_event_id_raises(tmp_path: Path) -> None:
    """Append-only (ADR-0001): the committed tree never overwrites a file.
    Re-writing the same observation event id raises, loudly."""
    store = CandidateFileStore(tmp_path / "candidates")
    ev = _risk_event(risk_id="idempotency")
    store.write(ev)
    with pytest.raises(FileExistsError):
        store.write(ev)


def test_goal_id_with_path_separator_is_refused(tmp_path: Path) -> None:
    """A goal id that would escape the candidates root is refused loudly,
    never silently landing a file outside the tree."""
    store = CandidateFileStore(tmp_path / "candidates")
    bad = _risk_event(goal_id="../escape", risk_id="x")
    with pytest.raises(ValueError):
        store.write(bad)
    with pytest.raises(ValueError):
        store.read("../escape")
