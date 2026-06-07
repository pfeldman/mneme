# Phase 1 regression-recall pre-registration

This file is the manifest of artifacts sealed BEFORE any arm of the
regression-recall experiment runs. Editing any of them post-run
invalidates the run and requires a new pre-registration ADR.

Pre-registered to defend against:
- p-hacking the kill criteria after seeing results.
- mid-experiment prompt tweaks that overfit to early arm performance.
- moving the `cold_readme` baseline to make memory's edge bigger.
- inventing post-hoc justifications for retaining a result the
  pre-registered gate would have killed.

## Sealed artifacts (commit BEFORE the first arm runs)

| Artifact | Path | What it fixes |
|----------|------|----------------|
| Ground-truth manifest | `manifest.json` | Which regressions are planted; the `expected_observation` per regression that the false-positive judge grades against. |
| Frozen README | `README_FROZEN.md` | The `cold_readme` arm's context. Authored without manifest access. |
| Per-goal sentences | `cold_readme_per_goal.md` | The one-sentence-per-goal grounding handed to the `cold_readme` arm. Same authoring constraint as the README. |
| E-mode prompt source | `src/praxis/runner/prompts.py` | The exploration-mode prompt the memory arm uses (rendered by `render_exploration_prompt`). The Python module sha is what gets pinned; a separate `.txt` would invite drift between docs and code. |
| Judge prompt | `judge_prompt.txt` | The false-positive adjudication prompt; its sha is logged with every judgment event. |
| Kill-criteria thresholds | `metrics.py` (the defaults to `evaluate`) | The exact sigma bounds and minimum gaps that decide continue / kill. |

## Recording at run time

The harness records the following in the run manifest BEFORE the first
arm fires:

```json
{
  "run_id": "...",
  "started_at": "...",
  "release": "phase-1-r1",
  "testapp_git_sha": "...",
  "frozen_readme_sha": "...",
  "cold_readme_per_goal_sha": "...",
  "manifest_sha": "...",
  "prompts_py_sha": "...",
  "judge_prompt_sha": "...",
  "metrics_sha": "...",
  "praxis_git_sha": "...",
  "model": "...",
  "model_provider": "...",
  "budget_tokens_per_goal": ...,
  "n_seeds": ...
}
```

After the run, the same JSON is committed alongside `results.json` and
`results.md` so a third party can verify which artifacts were active.

## Run procedure

1. Calibrate the budget on a CLEAN release (no planted regressions)
   using the memory arm's R-mode happy-path cost, targeting 95%
   utilization. Commit the calibrated budget to `budget.json`.
2. Pin the testapp git sha + the praxis git sha. Both go into the run
   manifest above. No code changes between this commit and the first
   arm run.
3. Pin all artifact shas in the run manifest. Commit the run manifest as
   `runs/<run_id>/manifest.json`.
4. Execute arms per the protocol in `PHASE_1_LOCAL_RUN.md`. Each arm
   produces `RunSummary` records persisted to
   `runs/<run_id>/summaries/<arm>/<seed>/<goal>.json`.
5. After all arms complete, compute aggregates + verdict via
   `metrics.evaluate`. Write `results.md` + `results.json` to
   `runs/<run_id>/`.
6. Record the verdict in `docs/adr/0010-phase-1-verdict.md`. If `kill`,
   the project returns to the kill/continue gate (docs/04).

## Threats to validity (mitigated, restated for the run record)

See `docs/phase-1-experiment.md` for the full list. Recapped here so the
recorded run manifest acknowledges them:

- Leakage between planter and seed author: 72-hour wash-out + lexical-
  overlap floor 30%.
- `cold_readme` strawman: frozen README + per-goal sentences authored
  without manifest access; an independent reviewer (Pablo role-playing
  the cold-arm advocate) signs off before runs begin.
- Budget calibrated against memory R-mode happy-path on a clean
  release, NOT against cold (no free E-mode lap).
- E-mode degenerating into R-mode: `off_path_fraction >= 0.4` is a
  HARD kill criterion, not a tuning knob.
- Judge bias: the judge prompt sha is logged with every event; 10% of
  judgments are re-graded by a second judge family post-run; high
  disagreement invalidates the run.
- Stale-trap visibility: the `s1_oracle_lies` regression has its own
  kill criterion (`stale_trap_recall >= 0.5`); a memory arm that
  blindly trusts its believed oracle will miss it.

## Out of scope for Phase 1 (deferred)

The following are deferred to Phase 1.5 / Phase 2 and are NOT part of
this pre-registration. Adding them mid-run is forbidden.

- Stagehand arm (deferred to Phase 1.5 with its own pre-registration).
- Auditor scenarios as R-mode input (rejected; moved to offline oracle
  stress).
- `refuted` status (rejected; violated ADR-0008 source-independence).
- Multi-writer concurrency (Phase 2).
- Real-app (OSS SPA) generalization (Phase 2).
