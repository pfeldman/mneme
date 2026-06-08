---
type: task-plan
ticket: null
mode: freetext
created: 2026-06-08
status: pending-approval
---

# Plan: Phase 3 implementation (library plus git, no SaaS) - ADRs 0018-0025

## Brief

Phase 3 ADRs 0018-0025 are Accepted on `main`. They were decisions only; no
Phase 3 feature is coded. This task writes the code. The eight ADRs plus the
`AGENTS.md` Phase 3 reframe ARE the spec. Build per ADR in dependency order,
three waves, `bash verify.sh` ALL GREEN per commit, one commit per logical
step, no push without Pablo's OK, no Co-Authored-By trailer.

## Pre-flight observations (grounded this session)

- `pyproject.toml` still says `name = "praxis"` (ADR-0020 changes it to
  `praxis-qa`; import name and CLI command stay `praxis`). Extras
  `browser-use`, `live`, `dev` already declared. Build backend hatchling,
  src layout.
- The current `.praxis/` layout in code (`src/praxis/cli/main.py`
  `ProjectContext` + `_cmd_init`) is `config.yaml` + `knowledge/` +
  `events/` + `reports/`, all gitignoring `events/` and `reports/`. ADR-0021
  wants a DIFFERENT tree: `config.yaml`, `knowledge/`, `candidates/<goal>/`,
  `runs/<timestamp>/`, `.praxisignore`, plus a root `.praxis.secrets`. The
  init/ProjectContext rewrite is a real migration, and `tests/test_cli.py`
  asserts the old shape, so it moves with the code.
- `FileEventStore` (`src/praxis/store/file_store.py`) keys events under
  `<root>/<tenant>/events/` and candidates under `<root>/<tenant>/candidates/`
  as `CandidateEvent` JSON. ADR-0021 wants committed candidate YAML one file
  per observation id under `.praxis/candidates/<goal>/<id>.yaml`. The
  one-file-per-id store property (ADR-0012) is load-bearing and already
  holds; the Phase 3 work bridges the committed YAML tree to it.
- The dedup-by-`trigger` projection already exists
  (`src/praxis/merge/candidates.py`); ADR-0023 report reuses it.
- Skills today live only in `.claude/skills/run-praxis-experiment/SKILL.md`
  (markdown + `name`/`description` frontmatter). Phase 3 ships the Praxis
  skills as PACKAGE DATA inside the wheel and `praxis init` scaffolds them;
  that round trip (skill-in-wheel -> `praxis init` -> `.claude/skills/`) is
  novel and the handoff flags it as must-prove.
- The agentic CLI today reads agent observations as JSON from stdin / file
  (`_executor_from_paste` / `_executor_from_file`). That executor IS the
  brain seam ADR-0019 formalizes; the body already imports with no LLM.

## Execution model

Three waves, dependency-ordered, all on ONE branch `phase-3-impl` off `main`
(Pablo chose single-branch + one PR at the end over branch-per-wave,
2026-06-08). One commit per step below, `bash verify.sh` ALL GREEN before
each commit. At the end, open ONE PR for the whole of Phase 3; merge to
`main` only on Pablo's explicit OK.

Sealed, MUST NOT regress: the Phase 0/1/2 experiments under `experiments/`
and their frozen run dirs, the schema (`schema/`), and the existing test
suite. Phase 3 ADRs themselves are Accepted and not re-edited except a
CHANGELOG implementation entry at the end.

DO NOT activate the deferred schema fields (`states`, `paths`) or introduce
a `refuted` status: those are Phase 1.5 (ADR-0018 section 5).

## Wave 1 - foundational substrate

- [ ] Step 1 (ADR-0020 packaging): rename + ship package data.
  `pyproject.toml` `[project] name = "praxis-qa"` (import name `praxis` and
  the `praxis` console script unchanged). Wire hatchling to ship
  `schema/knowledge.schema.json` and a new `src/praxis/skills/` tree as
  package data inside the wheel. Add `src/praxis/resources.py`: an
  `importlib.resources` accessor that resolves the packaged schema and the
  skills dir at runtime (works installed and from the src tree). Seed
  `src/praxis/skills/` with a `praxis` namespace placeholder skill so the
  scaffold path is exercisable before Wave 2 fills the real skills.
  - Files: `pyproject.toml`, `src/praxis/resources.py`,
    `src/praxis/skills/` (new), `tests/test_packaging.py` (new)
  - Verification: `pip install -e .` still works; a test asserts the
    packaged schema resolves and is byte-identical to `schema/knowledge.schema.json`;
    a test asserts the skills dir resolves and is non-empty; `bash verify.sh`
    ALL GREEN.

- [ ] Step 2 (ADR-0021 layout + init): migrate the `.praxis/` tree and init.
  Rewrite `ProjectContext` and `_cmd_init` to the ADR-0021 layout
  (`config.yaml`, `knowledge/`, `candidates/`, `runs/<timestamp>/`,
  `.praxisignore`). `praxis init` creates the tree, appends `.praxis/runs/`
  AND `.praxis.secrets` to the repo `.gitignore` (creating it if absent,
  idempotent), and scaffolds the skills from package data (Step 1) into
  `.claude/skills/`. The per-run raw event log goes under
  `runs/<timestamp>/`; candidates and knowledge are the committed tree.
  Update `tests/test_cli.py` to the new shape.
  - Files: `src/praxis/cli/main.py`, `tests/test_cli.py`,
    `tests/test_init_layout.py` (new)
  - Verification: `praxis init` in a temp dir produces the exact ADR-0021
    tree; `.gitignore` contains both `.praxis/runs/` and `.praxis.secrets`
    and re-running init does not duplicate the lines; `.claude/skills/`
    contains the scaffolded skill(s); `.praxis.secrets` is gitignored BEFORE
    any secret could be written; `bash verify.sh` ALL GREEN.

- [ ] Step 3 (ADR-0021 secrets channel): the loader + ask-or-fail.
  Add `src/praxis/secrets.py`: load credentials with an environment variable
  winning over the `.praxis.secrets` `KEY=value` file; a `MissingCredential`
  exception naming the absent key; a console/CI helper that fails LOUDLY
  (non-zero, names the key and how to set it, no prompt) and a hook the skill
  surface uses to ask and offer `! echo "KEY=value" >> .praxis.secrets`. A
  secret value is never echoed back to stdout or a log.
  - Files: `src/praxis/secrets.py` (new), `tests/test_secrets.py` (new)
  - Verification: env var beats file; absent key raises `MissingCredential`
    with the key name; the console helper exits non-zero and names the key;
    no test observes a secret value echoed; `bash verify.sh` ALL GREEN.

- [ ] Step 4 (ADR-0021 committed candidate tree): one-file-per-id writes.
  Add a writer that persists explore candidate risks/uncertainties as
  `.praxis/candidates/<goal>/<observation_id>.yaml`, one file per observation
  (never a shared mutable list), named by the content-addressable event id
  (ADR-0012). Wire `praxis explore` and `praxis review` to the committed
  tree; dedup/corroboration stays at the projection by `trigger`
  (`src/praxis/merge/candidates.py`, unchanged logic). `praxis review`
  reads the committed candidate files for the aggregate queue.
  - Files: `src/praxis/cli/main.py` (explore + review wiring), a candidate
    writer module (new, e.g. `src/praxis/store/candidate_files.py`),
    `tests/test_candidate_files.py` (new)
  - Verification: two candidate adds for one goal are two files, merge-safe
    (no shared edited line); two observations of the same finding are two
    files sharing one `trigger`, deduped only at projection; a concurrent
    two-writer test proves no candidate is lost; `bash verify.sh` ALL GREEN.

- [ ] Step 5 (ADR-0019 brain seam + dual surface): factor the engine.
  Extract the regress/explore execution into a reusable engine callable from
  (a) the console CLI and (b) a skill driver, with the brain/executor as the
  pluggable seam (brain-agnostic, no LLM import in the core path). Formalize
  the deterministic-vs-agentic split in code: `init`/`status`/`review` stay
  brain-free; `regress`/`explore` take a brain via the executor seam. Keep
  the body fully importable and testable with no brain present.
  - Files: `src/praxis/cli/main.py`, `src/praxis/runner/` (engine factoring),
    `tests/test_brain_seam.py` (new)
  - Verification: the engine runs from a console entry and from a
    direct-call (skill) entry against the same store with the same verdict;
    `import praxis` and the body tests pass with no LLM installed; no brain
    choice is written into knowledge; `bash verify.sh` ALL GREEN.

## Wave 2 - operations

- [ ] Step 6 (ADR-0023 regress aggregate + verdict): default-all + break-vs-drift.
  `praxis regress` with no `--goal` runs every goal under `.praxis/knowledge/`
  and emits ONE aggregate markdown report under `.praxis/runs/<timestamp>/`.
  Per-goal verdict is OK / REGRESSED / STALE, each shipped with its evidence
  (the flipped signal, the ADR-0013 version anchor for STALE). A REGRESSED or
  ERROR goal fails the run LOUDLY (non-zero exit, named goal + named signal);
  one REGRESSED never hides behind a "mostly green" roll-up. Per-goal budget
  slice (token + wall-time); a goal that exhausts its slice is a loud ERROR,
  not a silent skip. R-mode keeps the ADR-0009 no-auditor-input closure.
  - Files: `src/praxis/runner/regression.py`, `src/praxis/runner/report.py`,
    `src/praxis/cli/main.py`, `tests/test_regress_aggregate.py` (new)
  - Verification: OK/REGRESSED/STALE classification each covered; a single
    REGRESSED goal makes the aggregate exit non-zero and names the signal; an
    errored goal is non-OK and fails the run; per-goal budget exhaustion is a
    loud ERROR; auditor scenarios are not an input; `bash verify.sh` ALL GREEN.

- [ ] Step 7 (ADR-0023 explore aggregate + candidate report): default-all + grouped.
  `praxis explore` with no `--goal` hunts off-happy-path across every believed
  goal, writes candidate files (Step 4) on both surfaces, and reports findings
  GROUPED by `trigger`: each finding once, annotated with observation count
  and DISTINCT `source_id` count (N same-`agent_identity` observations = ONE
  source, ADR-0008). Keep `off_path_fraction` floor logging.
  - Files: `src/praxis/runner/exploration.py`, `src/praxis/runner/report.py`,
    `src/praxis/cli/main.py`, `tests/test_explore_aggregate.py` (new)
  - Verification: aggregate explore writes one file per observation; the
    report groups by trigger with correct source counts; same-model duplicates
    collapse to one source; `bash verify.sh` ALL GREEN.

- [ ] Step 8 (ADR-0023 regress/explore skills): author + ship + scaffold.
  Author `/praxis:regress` and `/praxis:explore` skill markdown into
  `src/praxis/skills/` (the local-brain surface: run the engine, then triage
  each non-OK goal - REGRESSED routes to "file a bug", STALE routes to a
  proposed re-seed - advisory only, never mutating committed knowledge;
  inline candidate triage for explore). Confirm they ship in package data and
  `praxis init` scaffolds them.
  - Files: `src/praxis/skills/praxis/regress/SKILL.md` (new),
    `src/praxis/skills/praxis/explore/SKILL.md` (new),
    `tests/test_packaging.py` (extend)
  - Verification: both skills resolve from package data and are scaffolded by
    `praxis init`; the skill text states triage is advisory and never mutates
    knowledge; `bash verify.sh` ALL GREEN.

- [ ] Step 9 (ADR-0022 teach supporting seams): the library half.
  Add the non-interactive seams the teach skill needs: emit a human-seeded
  goal YAML (success oracle `source_type = human`, provenance + confidence
  mandatory); record only the ADR-0017 abstract `auth_state` (authenticated +
  scope), never the credential (reuse Step 3 loader); the no-silent-overwrite
  rule (a re-teach of a believed goal appends a contested candidate refinement
  under `.praxis/candidates/`, never an in-place edit); the dual end condition
  (happy-path-observed AND human-confirm) with a budget + wall-time backstop
  that, on non-convergence, writes NO goal and emits a loud, traceable
  not-converged event.
  - Files: a teach support module (new, e.g. `src/praxis/teach/session.py`),
    `tests/test_teach_session.py` (new)
  - Verification: a confirmed session emits a `source_type=human` seed with
    provenance + confidence; a re-teach of a believed goal produces a
    contested candidate, not a mutation; a non-converged session writes no
    goal and emits the not-converged event; no credential is ever persisted;
    `bash verify.sh` ALL GREEN.

- [ ] Step 10 (ADR-0022 teach skill): author + ship + scaffold.
  Author `/praxis:teach` skill markdown into `src/praxis/skills/`: the typed
  prompt protocol (exactly one of credential / navigation-hint / role /
  confirmation per question), the invariants-not-coordinates rule on
  navigation hints, the dual end condition, the human-seeded output, and the
  credentials-never-persisted contract. Ship in package data; `praxis init`
  scaffolds it.
  - Files: `src/praxis/skills/praxis/teach/SKILL.md` (new),
    `tests/test_packaging.py` (extend)
  - Verification: the teach skill resolves from package data and scaffolds;
    the skill text encodes the four typed-prompt types and the dual end
    condition; `bash verify.sh` ALL GREEN.

- [ ] Step 11 (ADR-0022 teach end-to-end proof): prove the novel loop on testapp.
  Drive the teach loop with the LOCAL Claude brain (this session's
  Playwright/headless path) against `experiments/ui-mutation/testapp.py`:
  natural-language intent -> explore -> typed prompts -> human confirm ->
  emitted seeded goal YAML. Confirm credentials never land in any file.
  Document the run under `tasks/phase-3-impl/teach-proof.md` (no secrets, no
  local-only identifiers leaked). This is the highest-risk item the handoff
  flags; it gates the wave.
  - Files: `tasks/phase-3-impl/teach-proof.md` (new), any seam fixes the run
    surfaces
  - Verification: a real teach run produces a valid `source_type=human`
    goal YAML that validates against the schema; the proof doc records the
    interaction; `.praxis.secrets`/logs contain no leaked secret;
    `bash verify.sh` ALL GREEN.

## Wave 3 - surface

- [ ] Step 12 (ADR-0024 example CI workflow): documented example, not a product.
  Ship ONE copy-paste GitHub Actions example that runs `praxis regress` as a
  PR/tag gate (fails on the loud non-zero exit) and, optionally, scheduled
  `praxis explore` that writes candidate files. It is explicitly an EXAMPLE
  the team adapts; Praxis ships no reusable action, never pushes, never opens
  a PR, never auto-promotes, never force-pushes, never runs teach in CI. The
  file lives in the docs site (Step 13/14).
  - Files: `docs/examples/ci/praxis-regress.yml` (new) + its docs page
  - Verification: the workflow runs `praxis regress` and gates on exit code;
    the surrounding docs state it is an example and the team owns push/PR/auth;
    no secret is echoed; `bash verify.sh` ALL GREEN.

- [ ] Step 13 (ADR-0025 docs site scaffold): mkdocs-material from `docs/`.
  Add `mkdocs.yml` (mkdocs-material) sourced from the existing `docs/` tree,
  published to GitHub Pages. NO analytics, NO signup, NO email capture, NO
  funnel. CTAs are `pip install praxis-qa` and the repo link. The only
  quantitative claim is the ADR-0010 Phase 1 number, presented provisional.
  The thesis is framed as the project's bet (ADR-0009), not a proven fact.
  mkdocs is a docs-only dev tool; declare it in a docs extra or a
  requirements file, not in the core deps (ask before adding to core).
  - Files: `mkdocs.yml` (new), `docs/index.md` (new landing), nav wiring
  - Verification: `mkdocs build` succeeds with no analytics/signup; the
    landing page makes no claim beyond the ADR-0010 number; `bash verify.sh`
    ALL GREEN.

- [ ] Step 14 (ADR-0025 example set pages): testapp, Conduit, one OSS app.
  Author the three example walkthroughs: testapp (the teach-then-regress loop
  end to end), Conduit (ADR-0016 real-app SUT), and one real public OSS app
  (the specific pick is an implementation choice - see Open decisions). Link
  the Step 12 example CI workflow page. Honest framing: demonstration, not a
  benchmark.
  - Files: `docs/examples/testapp.md`, `docs/examples/conduit.md`,
    `docs/examples/<oss-app>.md` (new)
  - Verification: three example pages build and render; no overclaim beyond
    the ADR-0010 number; the example set is described as a demonstration;
    `bash verify.sh` ALL GREEN.

## Integration + wrap

- [ ] Step 15 (cross-feature stress + Conduit bring-up + CHANGELOG):
  Run a real cross-feature integration: multi-writer + decay + candidates +
  the new `.praxis/` layout firing together against a live target; record the
  result under `tasks/phase-3-impl/integration-run.md`. Run the Conduit
  docker bring-up end-to-end once (`PRAXIS_RUN_CONDUIT_BRINGUP=1`) and record
  the outcome (the handoff notes nobody has). Add a CHANGELOG.md entry for the
  Phase 3 IMPLEMENTATION (distinct from the existing ADR-decision entry).
  - Files: `tasks/phase-3-impl/integration-run.md` (new), `CHANGELOG.md`
  - Verification: the integration run completes and loses no knowledge across
    the features; the Conduit bring-up result is recorded (pass or a named
    failure, not silently skipped); CHANGELOG has the implementation entry;
    `bash verify.sh` ALL GREEN.

## Risks / unknowns

- Skill-in-wheel -> `praxis init` -> `.claude/skills/` round trip is novel
  for a pip package. Steps 1-2 build it; Step 8/10 fill it; it is proven only
  when `praxis init` from an installed wheel scaffolds a working skill.
- The teach interactive loop (driving a real browser while blocking on a
  human answer) has no Phase 0-2 precedent. Step 9 builds the testable seams;
  Step 11 is the real-browser proof and carries the residual risk.
- The `.praxis/` layout migration (Step 2) changes init, ProjectContext, the
  candidate path, and `tests/test_cli.py` together. Risk of regressing the
  Phase 1/2 store wiring; the concurrency and append-only tests are the guard.
- `.praxis.secrets` MUST be gitignored by init before any secret is written
  (Step 2). The whole secret contract rests on it.
- mkdocs is a new dev/docs dependency. It is docs-only and must NOT enter the
  core install (ADR-0020 forbids deps beyond pydantic/pyyaml + declared
  extras without an ADR); a docs extra or a separate requirements file is the
  path. Flagged as an Open decision.

## Open decisions (resolve at the gate)

1. **Worktrees vs single branch.** Recommended: a branch per wave off `main`,
   merged sequentially on your OK (the Phase 2 pattern, low coordination for
   one builder). The handoff mentions parallel worktrees; with one builder a
   branch per wave is simpler. Pick: branch-per-wave (recommended) or one
   `phase-3-impl` branch with a PR at the end.
2. **The one real OSS app for the docs example set (Step 14).** ADR-0025
   fixes that there is exactly one real public app but defers the pick.
   Proposal: choose at Step 14 from a shortlist and flag it then, rather than
   block the plan now.
3. **mkdocs dependency placement (Step 13).** Proposal: a `docs` optional
   extra in `pyproject.toml` (keeps the core install pydantic + pyyaml only).
   Confirm that is acceptable, or prefer a standalone `docs/requirements.txt`.
