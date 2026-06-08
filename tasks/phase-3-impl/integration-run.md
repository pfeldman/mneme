# Step 15: cross-feature integration run

The offline cross-feature stress the handoff flagged as never having been done
together. Each Phase 2 / Phase 3 feature has its own focused suite
(`test_multi_writer.py`, `test_merge_decay.py`, `test_candidate_files.py`,
`test_init_layout.py`), but until this step nothing proved they COMPOSE on one
`.praxis/` project without one feature corrupting another.

The run is the test `tests/test_integration_phase3.py::test_phase3_features_compose_on_one_praxis_project`.
It is deterministic and fully offline: no browser, no LLM, no docker. Every
clock is injected (a single fixed `NOW`), so the believed projection, the decay
anchors, and the candidate timestamps are reproducible across machines. It runs
under `bash verify.sh` as part of the normal pytest step.

## What it exercises (the features firing together)

One `.praxis/` project in a tmp_path, built by the real `praxis init` code path
(`_cmd_init` + `ProjectContext`), then layered with four features at once:

1. The ADR-0021 `.praxis/` layout. `init` produces the committed tree
   (`config.yaml`, `knowledge/`, `candidates/`) plus the gitignored per-machine
   `runs/<timestamp>/` log, and gitignores `.praxis.secrets` before any secret
   could be written. The test asserts the tree exists and that the secrets file
   is ignored but not yet present.

2. Multiple concurrent writers on the append-only store. Two DISTINCT agent
   identities each append 40 observation events to the per-machine event log
   `ProjectContext.store()` hands out (the real `RunsEventStore` the CLI uses,
   not a throwaway). They run behind a barrier to maximize overlap.

3. Recency decay via the explicit decay-event path. The same concurrent log is
   projected at a later app version (1.4.0, three minors past the 1.0.0 the
   evidence was seen at) through `project_with_decay`. The version anchor stales
   the aged evidence and the projection emits explicit `DecayEvent`s. Decay is a
   projection derivation; the store is never mutated.

4. One-file-per-id candidates from two writers for the SAME goal. Two different
   agents observe one shared finding (same risk id, same trigger), one agent
   re-observes a distinct finding five times across both trigger kinds, and a
   third agent raises an uncertainty. All are written through the committed
   `CandidateFileStore` (`.praxis/candidates/<goal>/`), again concurrently
   behind a barrier, one YAML file per observation event id. Dedup and
   corroboration happen ONLY at the projection, by trigger / id.

## The assertions that prove no knowledge is lost and the features compose

- No lost observations: after the concurrent appends the store folds back
  exactly 80 events, evenly split 40 / 40 between the two agent identities (no
  writer's events erased). This is the AGENTS.md DoD requirement for any change
  that touches the store.

- No lost candidates: after the concurrent candidate writes the disk holds one
  YAML file per observation event id (file stems equal the event ids), and the
  read folds back every candidate. The shared mutable list that would reintroduce
  last-write-wins erasure is never used.

- Decay is loud and traceable: every emitted decay event carries
  `to_status=stale`, `from_status=believed`, a retired event id set, the 1.4.0
  anchor, and a version-or-both rule. The fresh projection (at 1.0.0) believes
  both diverse signals; the late projection (at 1.4.0) flips them to stale and
  believes none.

- N same-agent observations count as ONE source (ADR-0008): the five-times
  same-`agent_identity` finding projects to one candidate with
  `distinct_source_ids == {agent-A}` and stays contested, even though it spans
  both trigger kinds (so type-diversity alone could have looked satisfied). The
  two-different-agent finding has two distinct sources but one evidence kind and
  is likewise contested. The trigger-grouped report tells the same story: source
  count is the distinct agent count, not the observation count, and nothing is
  believed by count alone.

- Internal consistency of the combined projection: the believed signal set, the
  contested candidate set, and the decayed / stale entries are exactly what the
  union of the event log and candidate tree implies. The candidate writes never
  leak into the signal projection (the diversity gate over signals never sees a
  candidate write, the ADR-0008 schema-drift defense): re-projecting the signal
  log still believes the two diverse signals, and re-projecting at the late
  version reproduces the same stale flip, proving decay is a pure derivation the
  candidate section did not disturb.

## Result

PASS. `bash verify.sh` ends ALL GREEN with the new integration test included in
the pytest step; the four features compose on one project with no knowledge lost
and no cross-feature corruption.

## Live Conduit bring-up (recorded separately)

The live Conduit docker bring-up (ADR-0016 C1, `PRAXIS_RUN_CONDUIT_BRINGUP=1`)
is a separate, networked exercise and is recorded on its own. It is NOT part of
this offline cross-feature test, which is deterministic and dependency-free by
design.

Conduit bring-up result (run 2026-06-08, docker 29.4.1, cold cache): FAILED, a
named upstream-image failure, NOT a Praxis defect. `bash bring_up.sh` exits
non-zero because the docker-compose stack references upstream images that are no
longer pullable from Docker Hub:

    gothinkster/realworld-node-express:latest -> pull access denied,
      repository does not exist or may require docker login
    gothinkster/realworld-react-redux:latest  -> interrupted (same cause)

The bring-up script, the 30-minute ceiling logic, and the `--check` probe all
work as designed (the second gated test, `--check` idempotency, passed). The
blocker is purely that the `gothinkster/realworld-*` images the experiment was
pinned to have disappeared from Docker Hub since ADR-0016 was written. The C1
bring-up needs its image references updated to a currently-published RealWorld
backend/frontend image (or a locally built one) before it can complete. This is
experiment-infra maintenance (the ADR-0016 SUT), out of the Phase 3
implementation scope; recorded loudly here rather than silently skipped so the
stale image pins are visible and fixable.
