"""`RunsEventStore` reconciles the file-per-event store with ADR-0021 runs/.

ADR-0021 decision 1 puts the per-machine append-only event log under
`.praxis/runs/<timestamp>/`, one subtree per session. Decision 3 keeps that log
the local source of truth and folds it into believed / contested state through
the projection. `RunsEventStore` bridges those two:

    - writes land in the CURRENT run subtree (append-only, file-per-event,
      ADR-0001 + ADR-0012);
    - reads fold across EVERY run subtree, so the projection sees the whole
      per-machine log even across separate CLI invocations.

These tests pin the write target, the cross-run read fold, the append-only
guarantee carried over from the base store, and a concurrent two-writer case
across two run subtrees that must lose no knowledge.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from praxis.merge import project
from praxis.model import HttpTrigger, Provenance, Risk, Target
from praxis.store import (
    CandidateEvent,
    CandidateRiskPayload,
    ObservationEvent,
    ObservedSignal,
    RunsEventStore,
    new_run_id,
    source_id_for,
)


def _sig(value: str, type_: str = "behavioral", src_id: str = "m::p") -> ObservedSignal:
    return ObservedSignal(kind="success", type=type_, value=value,
                          source_type="agent", source_id=src_id,
                          observed_app_version="1")


def test_writes_land_in_the_current_run_subtree(tmp_path) -> None:
    run_id = new_run_id()
    store = RunsEventStore(tmp_path, run_id)
    store.append(ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("x")]))
    events_dir = tmp_path / run_id / "local" / "events"
    assert events_dir.is_dir()
    assert len(list(events_dir.glob("*.json"))) == 1


def test_reads_fold_across_all_run_subtrees(tmp_path) -> None:
    # Run 1 writes one event into its own subtree.
    run1 = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc)))
    run1.append(ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("run1-signal")]))

    # Run 2 writes a second event into a DIFFERENT subtree, then reads.
    run2 = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 2, tzinfo=timezone.utc)))
    run2.append(ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("run2-signal")]))

    # The fold across both subtrees sees both events, time-ordered.
    values = [e.signals[0].value for e in run2.read(goal_id="g")]
    assert values == ["run1-signal", "run2-signal"]
    # Run 1's own store also reads across both (the fold is symmetric over the
    # on-disk set, ADR-0012).
    assert {e.signals[0].value for e in run1.read(goal_id="g")} == {
        "run1-signal", "run2-signal"
    }


def test_candidates_fold_across_runs(tmp_path) -> None:
    # A candidate written in run 1 is visible to a later run reading across all.
    prov = Provenance(source_type="agent", source_id="m::p",
                      last_verified=datetime(2026, 6, 1, tzinfo=timezone.utc),
                      observation_count=1)
    risk = Risk(id="r1", description="login redirects off-origin",
                trigger=HttpTrigger(method="GET", path="/cb",
                                     expect="Location matches origin"),
                status="contested", confidence=0.6, provenance=prov)
    run1 = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc)))
    run1.append_candidate(CandidateEvent(
        agent_identity="m::p", goal_id="g",
        payload=CandidateRiskPayload(risk=risk),
    ))
    run2 = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 2, tzinfo=timezone.utc)))
    cands = run2.read_candidates(goal_id="g")
    assert len(cands) == 1
    assert cands[0].payload.risk.id == "r1"


def test_append_only_carries_over(tmp_path) -> None:
    # The base append-only guarantee is unchanged: re-appending the same event
    # id raises rather than overwriting (ADR-0001).
    store = RunsEventStore(tmp_path, new_run_id())
    ev = ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("x")])
    store.append(ev)
    with pytest.raises(FileExistsError):
        store.append(ev)


def test_concurrent_writes_across_runs_lose_no_knowledge(tmp_path) -> None:
    """Two agents writing into two distinct run subtrees concurrently lose no
    knowledge, and the projection over the folded log retains BOTH evidence
    types (the cross-run analog of the ADR-0012 concurrency guarantee)."""
    run_a = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc)))
    run_b = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 2, tzinfo=timezone.utc)))
    n = 40

    def writer(store: RunsEventStore, model: str, type_: str) -> None:
        src = source_id_for(model=model, prompt_lineage="phase-3-prompt-v1")
        for i in range(n):
            store.append(ObservationEvent(
                agent_id=src, goal_id="g",
                signals=[ObservedSignal(
                    kind="success", type=type_, value=f"{type_} signal seen",
                    source_type="agent", source_id=src, observed_app_version="1",
                )],
            ))

    t1 = threading.Thread(target=writer, args=(run_a, "claude-a", "behavioral"))
    t2 = threading.Thread(target=writer, args=(run_b, "claude-b", "network"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Every event landed; the fold across both subtrees sees all 2n.
    folded = run_a.read(goal_id="g")
    assert len(folded) == 2 * n
    # Two distinct sources of two evidence types -> believed (diversity gate).
    kf = project(folded, goal_id="g", goal="g", target=Target(app="t"))
    believed = [s for s in kf.success_signals if s.status.value == "believed"]
    assert believed, "diversity over two run subtrees must reach believed"


# --- ADR-0035 decision 8: run dirs may be `<timestamp>__<env>` -------------


def test_reads_fold_across_suffixed_and_unsuffixed_run_dirs(tmp_path) -> None:
    """One runs/ tree mixing BOTH dir shapes folds into ONE log (ADR-0035
    decision 8): a declared project writes `runs/<timestamp>__<env>/` while its
    pre-declaration history sits in bare `runs/<timestamp>/` dirs. The store
    treats run-dir names opaquely (sorted iterdir), so the cross-run read sees
    every subtree regardless of shape, in (ts, event_id) order."""
    # A bare pre-declaration run dir...
    bare = RunsEventStore(
        tmp_path, new_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc)))
    bare.append(ObservationEvent(
        agent_id="m::p", goal_id="g", signals=[_sig("bare-run-signal")]))
    # ...two env-suffixed run dirs (the ADR-0035 declared shape)...
    dev2 = RunsEventStore(
        tmp_path,
        new_run_id(datetime(2026, 6, 2, tzinfo=timezone.utc)) + "__dev2")
    dev2.append(ObservationEvent(
        agent_id="m::p", goal_id="g", signals=[_sig("dev2-run-signal")]))
    prod = RunsEventStore(
        tmp_path,
        new_run_id(datetime(2026, 6, 3, tzinfo=timezone.utc)) + "__prod")
    prod.append(ObservationEvent(
        agent_id="m::p", goal_id="g", signals=[_sig("prod-run-signal")]))

    # Each suffixed dir exists on disk with the suffix in its NAME.
    assert (tmp_path / dev2.run_id).name.endswith("__dev2")
    assert (tmp_path / prod.run_id / "local" / "events").is_dir()

    # The fold from ANY of the three stores sees all three events, time-ordered:
    # the dir-name shape never partitions the read (partitioning is the
    # adapter's environment FIELD filter, never the path, ADR-0035 decision 4).
    expected = ["bare-run-signal", "dev2-run-signal", "prod-run-signal"]
    for store in (bare, dev2, prod):
        assert [e.signals[0].value for e in store.read(goal_id="g")] == expected


def test_candidates_fold_across_suffixed_and_unsuffixed_run_dirs(tmp_path) -> None:
    """The candidate fold crosses both dir shapes too: a candidate written in a
    bare run dir is visible to a later env-suffixed run, and vice versa."""
    bare = RunsEventStore(
        tmp_path, new_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc)))
    bare.append_candidate(CandidateEvent(
        agent_identity="m::p", goal_id="g",
        payload=CandidateRiskPayload(risk=_risk("r-bare")),
    ))
    suffixed = RunsEventStore(
        tmp_path,
        new_run_id(datetime(2026, 6, 2, tzinfo=timezone.utc)) + "__prod")
    suffixed.append_candidate(CandidateEvent(
        agent_identity="m::p", goal_id="g", environment="prod",
        payload=CandidateRiskPayload(risk=_risk("r-prod")),
    ))
    for store in (bare, suffixed):
        ids = [c.payload.risk.id for c in store.read_candidates(goal_id="g")]
        assert ids == ["r-bare", "r-prod"]


# --- environment field on candidates (ADR-0035 decisions 4 + 6): the env rides
# as provenance on the event, the shared candidate layout is unchanged, and a
# pre-ADR-0035 candidate JSON (no `environment` key) keeps parsing.


def _risk(rid: str = "r1") -> Risk:
    prov = Provenance(source_type="agent", source_id="m::p",
                      last_verified=datetime(2026, 6, 1, tzinfo=timezone.utc),
                      observation_count=1)
    return Risk(id=rid, description="login redirects off-origin",
                trigger=HttpTrigger(method="GET", path="/cb",
                                    expect="Location matches origin"),
                status="contested", confidence=0.6, provenance=prov)


def test_candidate_event_environment_round_trips(tmp_path) -> None:
    run = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc)))
    run.append_candidate(CandidateEvent(
        agent_identity="m::p", goal_id="g", environment="dev2",
        payload=CandidateRiskPayload(risk=_risk()),
    ))
    cands = run.read_candidates(goal_id="g")
    assert len(cands) == 1
    assert cands[0].environment == "dev2"


def test_candidate_event_environment_defaults_none(tmp_path) -> None:
    run = RunsEventStore(tmp_path, new_run_id(datetime(2026, 6, 1, tzinfo=timezone.utc)))
    run.append_candidate(CandidateEvent(
        agent_identity="m::p", goal_id="g",
        payload=CandidateRiskPayload(risk=_risk()),
    ))
    assert run.read_candidates(goal_id="g")[0].environment is None


def test_pre_environment_candidate_json_still_parses() -> None:
    """A literal pre-ADR-0035 candidate dict - no `environment` key - must
    validate, with the field defaulting to None (ADR-0001: additive only)."""
    ev = CandidateEvent.model_validate({
        "event_id": "c1",
        "ts": "2026-06-01T00:00:00+00:00",
        "schema_version": "0",
        "agent_identity": "m::p",
        "goal_id": "g",
        "observed_app_version": "1",
        "payload": {
            "kind": "candidate_risk",
            "risk": {
                "id": "r1",
                "description": "login redirects off-origin",
                "trigger": {"kind": "http", "method": "GET", "path": "/cb",
                            "body_or_params": None,
                            "expect": "Location matches origin"},
                "mitigation": None,
                "provenance": {"source_type": "agent", "source_id": "m::p",
                               "observed_app_version": None,
                               "last_verified": "2026-06-01T00:00:00+00:00",
                               "observation_count": 1},
                "confidence": 0.6,
                "status": "contested",
            },
        },
    })
    assert ev.environment is None
