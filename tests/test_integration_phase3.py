"""Phase 3 cross-feature integration (Wave 3 Step 15).

ONE deterministic, offline (no browser, no LLM, no docker) integration test
that fires the Phase 3 + Phase 2 features TOGETHER against a single `.praxis/`
project built in a tmp_path. The handoff flagged this multi-feature run as
never having been done together; each feature has its own focused suite
(`test_multi_writer.py`, `test_merge_decay.py`, `test_candidate_files.py`,
`test_init_layout.py`), but nothing proved they compose without one feature
corrupting another.

The features under one project, all at once:

  - the ADR-0021 `.praxis/` layout (config.yaml + knowledge/ + candidates/ +
    runs/<timestamp>/) as produced by `praxis init` / `ProjectContext`;
  - MULTIPLE concurrent writers appending observations to the append-only
    per-machine event log (the AGENTS.md DoD: when the store is touched, prove
    two agents' concurrent writes lose no knowledge);
  - recency DECAY applied via the explicit decay-event path
    (`project_with_decay`): a stale signal decays as the version anchor says;
  - CANDIDATES written one-file-per-id under `.praxis/candidates/<goal>/` by two
    DIFFERENT writers for the SAME goal, then deduped / corroborated at the
    projection BY TRIGGER, with N same-`agent_identity` observations counting as
    ONE source (ADR-0008);
  - an assertion that the believed projection over all of the above is
    internally consistent: the believed set, the contested candidates, and the
    decayed / stale entries are exactly what the combined event log implies.

Determinism: every clock is injected (a fixed `NOW`); no `datetime.now()` is
read in the assertions. This mirrors how `test_merge_decay.py` pins `NOW` and
`test_multi_writer.py` pins `source_id_for` so the projection is reproducible.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from praxis.cli.main import ProjectContext, _cmd_init
from praxis.merge import (
    DecayConfig,
    project,
    project_candidates,
    project_with_decay,
)
from praxis.model import (
    HttpTrigger,
    Provenance,
    Risk,
    SequenceTrigger,
    SourceType,
    Status,
    Target,
    Uncertainty,
)
from praxis.runner.report import group_candidates_by_trigger
from praxis.store import (
    CandidateEvent,
    CandidateRiskPayload,
    CandidateUncertaintyPayload,
    ObservationEvent,
    ObservedSignal,
    source_id_for,
)
from praxis.store.candidate_files import CandidateFileStore

# A fixed wall clock pins the whole run: decay anchors, candidate timestamps,
# and the believed projection are all derived from this single `now`, so the
# verdicts are reproducible across machines (no `datetime.now()` is consulted).
NOW = datetime(2026, 6, 8, tzinfo=timezone.utc)
GOAL = "checkout"


# --- argparse shim --------------------------------------------------------


class _InitArgs:
    """Minimal stand-in for the argparse Namespace `_cmd_init` consumes.

    Mirrors the `init` subparser defaults in `cli/main.py` so the test drives
    the SAME init code path the console `praxis init` does, building the real
    ADR-0021 tree (no hand-rolled directory layout)."""

    def __init__(self, root: Path) -> None:
        self.path = str(root)
        self.base_url = "http://127.0.0.1:8000"
        self.app = "acme"
        self.env = None
        self.agent_id = "praxis-cli"
        self.force = False


# --- fixtures (mirrors the per-feature suites' helpers) -------------------


def _sig(
    value: str,
    type_: str,
    src_id: str,
    *,
    kind: str = "success",
    ver: str = "1.0.0",
) -> ObservedSignal:
    return ObservedSignal(
        kind=kind,  # type: ignore[arg-type]
        type=type_,  # type: ignore[arg-type]
        value=value,
        present=True,
        source_type="agent",
        source_id=src_id,
        observed_app_version=ver,
    )


def _obs_event(
    *signals: ObservedSignal,
    agent: str,
    ts: datetime,
    ver: str = "1.0.0",
) -> ObservationEvent:
    return ObservationEvent(
        agent_id=agent, goal_id=GOAL, ts=ts, observed_app_version=ver,
        signals=list(signals),
    )


def _risk(
    risk_id: str,
    agent_identity: str,
    *,
    trigger_kind: str = "sequence",
) -> Risk:
    """A candidate Risk for `risk_id`.

    The structured trigger's CONTENT is keyed off `risk_id` so two distinct
    findings never accidentally share a trigger (the report groups by trigger
    content, not by id). Two observations of the SAME finding (same risk_id,
    same trigger_kind) DO share one trigger, which is exactly the dedup unit.
    """
    trigger: SequenceTrigger | HttpTrigger
    if trigger_kind == "http":
        trigger = HttpTrigger(
            method="POST", path=f"/{risk_id}",
            expect=f"{risk_id}: returns 200 with same order_id",
        )
    else:
        trigger = SequenceTrigger(
            n=2, action=f"submit {risk_id} with same Idempotency-Key",
            expect=f"{risk_id}: two distinct order_ids returned",
        )
    return Risk(
        id=risk_id,
        description=f"{risk_id}: same Idempotency-Key creates two orders",
        trigger=trigger,
        provenance=Provenance(
            source_type=SourceType.AGENT,
            source_id=agent_identity,
            last_verified=NOW,
            observation_count=1,
        ),
        confidence=0.7,
        status=Status.CONTESTED,
    )


def _risk_event(
    risk_id: str,
    agent_identity: str,
    *,
    trigger_kind: str = "sequence",
    ts: datetime | None = None,
) -> CandidateEvent:
    return CandidateEvent(
        ts=ts or NOW,
        agent_identity=agent_identity,
        goal_id=GOAL,
        payload=CandidateRiskPayload(
            risk=_risk(risk_id, agent_identity, trigger_kind=trigger_kind),
        ),
    )


def _uncertainty_event(
    unc_id: str, agent_identity: str, *, ts: datetime | None = None,
) -> CandidateEvent:
    return CandidateEvent(
        ts=ts or NOW,
        agent_identity=agent_identity,
        goal_id=GOAL,
        payload=CandidateUncertaintyPayload(
            uncertainty=Uncertainty(
                id=unc_id, question="how long is the receipt URL valid?",
                raised_by=agent_identity, raised_at=NOW,
            ),
        ),
    )


# --- the one cross-feature integration test -------------------------------


def test_phase3_features_compose_on_one_praxis_project(tmp_path: Path) -> None:
    """The whole of Phase 3's substrate firing at once against one project.

    Section by section the test layers a feature onto the SAME `.praxis/`
    project and, at the end, asserts the combined believed projection is
    exactly what the union of the event log + candidate tree implies. No
    section's output corrupts another's: concurrent writes do not drop a decay
    event, decay does not erase a candidate, candidates do not leak into the
    signal projection.
    """
    # === Section 1: the ADR-0021 layout, from the real init code path =======
    rc = _cmd_init(_InitArgs(tmp_path))
    assert rc == 0
    proj = ProjectContext(tmp_path)
    pdir = tmp_path / ".praxis"
    # The committed tree + the gitignored per-machine run log all exist.
    assert (pdir / "config.yaml").is_file()
    assert proj.knowledge_dir.is_dir()
    assert proj.candidates_dir.is_dir()
    assert proj.runs_dir.is_dir()
    # The secrets file is gitignored from the moment init ran, before any
    # secret could be written (ADR-0021 decision 6); no secret exists on disk.
    assert ".praxis.secrets" in (tmp_path / ".gitignore").read_text()
    assert not (tmp_path / ".praxis.secrets").exists()

    # === Section 2: multiple concurrent writers, append-only store ==========
    # Two DISTINCT agent identities each append many observation events to the
    # per-machine append-only log under runs/<timestamp>/. The store is the one
    # `ProjectContext.store()` hands out (the real RunsEventStore the CLI uses),
    # so this is the wired store, not a throwaway. DoD requirement when the
    # store is touched: concurrent writes lose NOTHING.
    store = proj.store()
    n_per_writer = 40
    src_a = source_id_for(model="model-a", prompt_lineage="phase-3-v1")
    src_b = source_id_for(model="model-b", prompt_lineage="phase-3-v1")
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _writer(agent: str, type_: str, value: str) -> None:
        barrier.wait()  # maximize the overlap window
        try:
            for i in range(n_per_writer):
                store.append(_obs_event(
                    _sig(value, type_, agent),
                    agent=agent, ts=NOW + timedelta(seconds=i),
                ))
        except BaseException as exc:  # noqa: BLE001 - surface any race failure
            errors.append(exc)

    threads = [
        threading.Thread(target=_writer,
                         args=(src_a, "behavioral", "logout becomes available")),
        threading.Thread(target=_writer,
                         args=(src_b, "network", "POST /session returns 2xx")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"concurrent writers raised: {errors}"

    events = store.read(GOAL)
    # Nothing lost: every event from both writers is folded back.
    assert len(events) == 2 * n_per_writer
    # Two distinct agent identities, evenly split (no writer's events erased).
    by_agent = {a: sum(1 for e in events if e.agent_id == a)
                for a in (src_a, src_b)}
    assert by_agent == {src_a: n_per_writer, src_b: n_per_writer}

    # The believed projection over the concurrent log: two DIFFERENT evidence
    # types (behavioral + network) from two DIFFERENT sources -> diversity-or-
    # seed promotes BOTH to believed (ADR-0008). This is the fresh-version
    # baseline the decay section will then age out.
    kf_fresh = project(
        events, goal_id=GOAL, goal="auth", target=Target(app="acme"),
        now=NOW, current_version="1.0.0",
    )
    assert {s.status.value for s in kf_fresh.success_signals} == {"believed"}
    assert len(kf_fresh.success_signals) == 2

    # === Section 3: recency decay via the explicit decay-event path =========
    # Project the SAME concurrent log at a later app version (1.4.0, three
    # minors past the 1.0.0 the evidence was seen at). The version anchor stales
    # the now-old evidence and the projection emits explicit DecayEvents
    # (ADR-0013). Decay is a projection derivation; the store is not mutated.
    kf_stale, decay_events = project_with_decay(
        events, goal_id=GOAL, goal="auth", target=Target(app="acme"),
        now=NOW, current_version="1.4.0", decay_config=DecayConfig(),
    )
    stale_statuses = {s.status.value for s in kf_stale.success_signals}
    assert "stale" in stale_statuses
    assert "believed" not in stale_statuses
    # Status flips emitted decay events, each loud-and-traceable: retired event
    # ids, the anchor, and the rule (ADR-0013 section 1).
    assert decay_events
    assert all(de.to_status == "stale" for de in decay_events)
    assert all(de.from_status == "believed" for de in decay_events)
    assert all(de.rule in ("version", "both") for de in decay_events)
    assert all(de.retired_event_ids for de in decay_events)
    assert all(de.anchor_current_version == "1.4.0" for de in decay_events)
    # The store is untouched by the projection: the log is the same 80 events.
    assert len(store.read(GOAL)) == 2 * n_per_writer

    # === Section 4: candidates one-file-per-id, two writers, same goal ======
    # Two DIFFERENT agents observe the SAME finding (same risk id, same trigger
    # kind) and each write a candidate file under .praxis/candidates/<goal>/.
    # In addition, ONE agent writes the SAME finding N times with mixed trigger
    # kinds, to prove N same-agent_identity observations still count as ONE
    # source at the projection (ADR-0008). Use the committed candidate store
    # the ProjectContext wires (CandidateFileStore over .praxis/candidates/).
    cand_store: CandidateFileStore = proj.candidate_files()

    # Two different agents, one shared finding "idempotency", same trigger kind.
    shared_a = _risk_event("idempotency", "agent-A", trigger_kind="sequence",
                           ts=NOW)
    shared_b = _risk_event("idempotency", "agent-B", trigger_kind="sequence",
                           ts=NOW + timedelta(seconds=1))
    # One agent re-observes a DISTINCT finding "double-charge" many times, with
    # both trigger kinds (so type-diversity alone could look satisfied), all
    # under the SAME agent_identity.
    same_agent_repeats = [
        _risk_event("double-charge", "agent-A",
                    trigger_kind=("http" if i % 2 else "sequence"),
                    ts=NOW + timedelta(seconds=10 + i))
        for i in range(5)
    ]
    # An uncertainty from a third agent, to exercise the uncertainty path too.
    unc = _uncertainty_event("receipt-window", "agent-C",
                             ts=NOW + timedelta(seconds=20))

    all_candidates = [shared_a, shared_b, *same_agent_repeats, unc]

    # Write them concurrently by two writers to prove the candidate tree is
    # also merge-safe under concurrency (one file per observation event id,
    # ADR-0021 decision 4): no candidate is lost.
    cand_errors: list[BaseException] = []
    cand_barrier = threading.Barrier(2)
    half = len(all_candidates) // 2
    chunks = [all_candidates[:half], all_candidates[half:]]

    def _cand_writer(chunk: list[CandidateEvent]) -> None:
        cand_barrier.wait()
        try:
            for ev in chunk:
                cand_store.write(ev)
        except BaseException as exc:  # noqa: BLE001 - surface any race failure
            cand_errors.append(exc)

    cthreads = [threading.Thread(target=_cand_writer, args=(c,)) for c in chunks]
    for t in cthreads:
        t.start()
    for t in cthreads:
        t.join()
    assert not cand_errors, f"concurrent candidate writers raised: {cand_errors}"

    # On disk: one file per observation event id, never a shared mutable list.
    goal_dir = proj.candidates_dir / GOAL
    files = sorted(goal_dir.glob("*.yaml"))
    assert len(files) == len(all_candidates)
    assert {p.stem for p in files} == {ev.event_id for ev in all_candidates}

    # No candidate lost across the concurrent write (DoD store-touch test).
    reloaded = cand_store.read(GOAL)
    assert {ev.event_id for ev in reloaded} == {ev.event_id for ev in all_candidates}

    # === Section 5: the believed projection is internally consistent ========
    # Dedup / corroboration happens ONLY at the projection, by trigger / id.
    projected = project_candidates(reloaded, goal_id=GOAL)
    by_id = {pc.candidate_id: pc for pc in projected}
    # Three distinct findings projected from the eight observation files: the
    # two shared "idempotency" files collapse to ONE, the five "double-charge"
    # same-agent files collapse to ONE, the lone uncertainty is the third.
    assert set(by_id) == {"idempotency", "double-charge", "receipt-window"}

    # "idempotency": two DISTINCT sources (agent-A, agent-B) but ONE evidence
    # kind (both sequence triggers) -> contested, never self-promoted on
    # same-type repeats (ADR-0008 type-diversity half of the gate).
    idem = by_id["idempotency"]
    assert idem.distinct_source_ids == {"agent-A", "agent-B"}
    assert idem.distinct_evidence_kinds == {"sequence"}
    assert idem.status == Status.CONTESTED
    assert len(idem.corroborating_events) == 2

    # "double-charge": five observations but all from ONE agent_identity, even
    # though they span BOTH trigger kinds. N same-agent observations are ONE
    # source (ADR-0008 source-independence half), so promotion never fires
    # regardless of the type spread.
    dbl = by_id["double-charge"]
    assert dbl.distinct_source_ids == {"agent-A"}
    assert dbl.status == Status.CONTESTED
    assert len(dbl.corroborating_events) == 5

    # The uncertainty: a single raiser, no second independent source -> the
    # question stays contested (believed-as-open needs a seed or a second
    # source, ADR-0014).
    rec = by_id["receipt-window"]
    assert rec.distinct_source_ids == {"agent-C"}
    assert rec.status == Status.CONTESTED

    # The trigger-grouped report (ADR-0023 decision 8) tells the same story the
    # projection does: each finding once, source count = DISTINCT agent count
    # (NOT observation count), nothing believed by count alone.
    groups = group_candidates_by_trigger(reloaded)
    # "idempotency" is one trigger group: 2 observations, 2 DISTINCT sources,
    # not believed (one evidence kind only).
    idem_groups = [g for g in groups if g.description.startswith("idempotency")]
    assert len(idem_groups) == 1
    ig = idem_groups[0]
    assert ig.observation_count == 2
    assert ig.source_count == 2  # distinct agent count, NOT observation count
    assert ig.believed is False
    # "double-charge" spans two trigger kinds -> two trigger groups, each from
    # the ONE agent (source_count 1), neither believed. Their observation counts
    # sum to the 5 same-agent writes (3 sequence + 2 http), proving N same-agent
    # observations are counted but never inflate the source count.
    dbl_groups = [g for g in groups if g.description.startswith("double-charge")]
    assert len(dbl_groups) == 2
    assert sum(g.observation_count for g in dbl_groups) == 5
    assert all(g.source_count == 1 for g in dbl_groups)
    assert all(not g.believed for g in dbl_groups)
    # No group is believed: nothing earned diversity-or-seed in this run.
    assert all(not g.believed for g in groups)

    # === Cross-feature non-interference invariants ==========================
    # The candidate writes did NOT leak into the signal event log: the store
    # still holds exactly the 80 observation events, and the believed signal
    # projection is unchanged by the candidate tree (the diversity gate over
    # signals never sees a candidate write, ADR-0008 schema-drift defense).
    assert len(store.read(GOAL)) == 2 * n_per_writer
    kf_after = project(
        store.read(GOAL), goal_id=GOAL, goal="auth", target=Target(app="acme"),
        now=NOW, current_version="1.0.0",
    )
    assert {s.status.value for s in kf_after.success_signals} == {"believed"}
    assert len(kf_after.success_signals) == 2

    # The decay verdict is reproducible: re-projecting at the late version
    # yields the same stale flip, proving decay derivation is pure and not a
    # one-shot mutation that the candidate section could have disturbed.
    kf_stale_again, decay_again = project_with_decay(
        store.read(GOAL), goal_id=GOAL, goal="auth", target=Target(app="acme"),
        now=NOW, current_version="1.4.0", decay_config=DecayConfig(),
    )
    assert {s.status.value for s in kf_stale_again.success_signals} == \
        {s.status.value for s in kf_stale.success_signals}
    assert {de.signal_value for de in decay_again} == \
        {de.signal_value for de in decay_events}
