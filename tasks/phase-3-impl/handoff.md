# Handoff - Praxis Phase 3 implementation

Fresh-session brief to implement Phase 3. The decisions are sealed in ADRs
0018-0025 (Accepted, on `main`); this phase writes the code.

## State of the world

- Phase 3 ADRs 0018-0025 are Accepted and on `main` (commit `2f133a8`),
  pushed to origin (github.com/pfeldman/mneme).
- `bash verify.sh` is ALL GREEN.
- Local branch `phase-3-adrs` is the checkpoint of the ADR-authoring work;
  do not delete it without Pablo's OK.
- This phase is IMPLEMENTATION. The ADRs were decisions only; no Phase 3
  feature is coded yet.

## The contract (read first)

`AGENTS.md` (now reframed: Phase 3 = library plus git, no SaaS) plus the
eight ADRs ARE the spec:

- 0018 scope + the library-plus-git reframe (no SaaS)
- 0019 brain pluggability and execution surfaces
- 0020 PyPI packaging and distribution
- 0021 the `.praxis/` repository convention (plus the `.praxis.secrets` channel)
- 0022 the teach operation as a Claude Code skill
- 0023 regress and explore dual surface and report
- 0024 CI integration by invoking the console commands
- 0025 landing page and docs site

`tasks/phase-3-adrs/task-plan.md` has the converged decisions in its approval
notes. `CHANGELOG.md` has a plain-English Phase 3 summary.

## Recommended decomposition (the part not in the ADRs)

Build per ADR, in dependency order, ideally in parallel worktrees merged
sequentially (the Phase 2 pattern). Three waves:

Wave 1, foundational (the substrate everything else needs):

- 0020 packaging: change `pyproject.toml` `[project] name` to `praxis-qa`
  (the import and the CLI command stay `praxis`); declare the extras; ship
  `schema/knowledge.schema.json` and the Claude Code skills as package data;
  make `praxis init` scaffold the skills into `.claude/skills/`.
- 0021 `.praxis/` convention: the directory layout (`config.yaml`,
  `knowledge/`, `candidates/`, `runs/`, `.praxisignore`); `praxis init`
  creates the tree and adds `.praxis/runs/` and `.praxis.secrets` to
  `.gitignore`; one-file-per-observation candidate writes; the secrets loader
  (an environment variable wins over the `.praxis.secrets` file); the
  missing-credential behavior (the skill asks and offers the
  `! echo "KEY=value" >> .praxis.secrets` command, the console and CI fail
  loudly).
- 0019 brain pluggability: the body-vs-brain split in code, the
  deterministic-vs-agentic classification, and the dual-surface plumbing (the
  same engine behind a console CLI and a Claude Code skill).

Wave 2, operations:

- 0022 teach skill: the interactive typed-prompt protocol (credential /
  navigation-hint / role / confirmation), the dual end-condition (happy path
  observed AND human confirm) with the budget plus wall-time backstop and the
  loud not-converged event, the human-seeded output, credentials that drive
  the browser and are never persisted, and the no-silent-overwrite of a
  believed goal.
- 0023 regress and explore: the console CLI (test-style, exit code) plus the
  Claude Code skill (triage); no `--goal` runs every believed goal and emits
  one aggregate report; the OK / REGRESSED / STALE verdict; candidate dedup by
  `trigger` at the projection; inline triage on the skill surface.

Wave 3, surface:

- 0024 CI: ship ONLY a documented example workflow (a minimal GitHub Actions
  file), not a reusable action; the team owns push / PR / auth.
- 0025 docs site: mkdocs-material from `docs/`, no analytics or signup, the
  example set (testapp, Conduit, one OSS app), and the example CI workflow
  lives here.

## Watch-items

- `praxis-qa` is a decision, not yet in `pyproject.toml` (the name is still
  `praxis` there). The packaging step makes the change.
- Shipping Claude Code skills inside a pip wheel and scaffolding them with
  `praxis init` is novel and untested. Prove it end-to-end.
- The teach interactive loop (driving a real browser while blocking on a human
  answer) has no precedent in Phase 0-2. It is the highest implementation risk.
- `.praxis.secrets` MUST be gitignored by `init` before any secret can be
  written. The whole secret contract rests on it.
- A cross-feature stress run (multi-writer plus decay plus candidates plus the
  new `.praxis/` layout firing together) was never done. Do a real integration
  run.
- The Conduit docker bring-up test exists but is gated behind
  `PRAXIS_RUN_CONDUIT_BRINGUP`; nobody has run it end-to-end.
- Do NOT activate the deferred schema fields (`states`, `paths`) or introduce
  a `refuted` status. Those are Phase 1.5.

## How to launch

A new `/task` (this is code; the ADR `/task` is done). It plans the
decomposition with an approval gate, then builds, committing per step with
`bash verify.sh` green each time, in worktrees per the waves above.

## Conventions

- Chat with Pablo in Spanish, short messages, one idea per turn. Pablo is a
  non-engineer for product evaluation: plain language, define terms, anchor
  with a concrete example.
- Code, commits, comments, and docs in English, ASCII only (the hooks reject
  em-dashes, smart quotes, ellipsis, Unicode arrows).
- One commit = one logical change. Never commit or push without Pablo's
  explicit OK.
- No Co-Authored-By trailer in this repo.
- Read `AGENTS.md` first.

<!-- /copy posted: 2026-06-08T12:49:51Z -->
