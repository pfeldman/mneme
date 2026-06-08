---
type: task-plan
ticket: null
mode: freetext
created: 2026-06-08
status: approved
approval_notes: |
  Pablo approved 2026-06-08 ("va solo, mandemos los 8"). The conversation
  before approval converged on decisions now baked into this plan:
  - The Praxis form factor is brain-pluggable. Praxis the library is the
    "body" (browser control, memory, deterministic CLI). The "brain" (the
    LLM that decides what to click and what to ask) is pluggable: local =
    Claude Code via skills (no API key, runs on Pablo's subscription); CI
    = an API-key agent (the `live` extra). This earned its own foundational
    ADR (0019), inserted right after the umbrella; the original seven ADRs
    shifted down by one (now 0020-0025).
  - Deterministic operations (init / status / review) are plain CLI; they
    do not reason, they only read and report. Agentic operations (teach /
    regress / explore) need a brain.
  - The teach operation is delivered ONLY as a Claude Code skill
    (`/praxis:teach`), never a bare CLI command, because it is always
    human-in-the-loop. Its output is human-seeded knowledge (the legitimate
    ADR-0005 seed path).
  - The regress and explore operations have a dual surface: a console CLI
    (test-style red/green, exit code, what CI uses and what you script) AND
    a Claude Code skill (same engine, plus Claude triaging failures). The
    regress output distinguishes OK / REGRESSED (the app broke, a real bug)
    / STALE (the app changed on purpose, the knowledge is outdated). That
    break-vs-drift triage is the core value over a plain test runner.
  - Skills ship with the package: `praxis init` scaffolds the Praxis Claude
    Code skills into the project's `.claude/skills/`. Owned by the
    packaging ADR (0020).
  - Minor defaults adopted with no objection: PyPI distribution name
    `praxis-qa` (import name and CLI command stay `praxis`); `.praxis/runs/`
    gitignored by default; landing page kept as its own ADR (0025); the
    teach operation ends on happy-path-observed AND user-confirm with a
    budget plus wall-time backstop; branch `phase-3-adrs` off `main`; no
    Co-Authored-By trailer.
---

# Plan: Phase 3 ADR decomposition (0018-0025) - the library-plus-git reframe

## Brief (synthesized from the handoff + Pablo 2026-06-08)

Phase 3 turns Praxis from "phase-gated research repo" into a shippable
library. Pablo rejected the SaaS framing on 2026-06-08 ("no es un
framework local, subido a pypi y ya? realmente necesitamos un saas?").
The new vision: **library + git, no SaaS**, with two halves:

- knowledge is shared through git (`git pull` brings the team the latest,
  `git push` shares discoveries), not a hosted backend
- the local brain is Claude Code (delivered as skills, no API key), the CI
  brain is an API-key agent (the GitHub Action)

A repo's knowledge lives under `.praxis/` (Pablo chose `.praxis/` over
`praxis/` on 2026-06-08: "less clutter, more serious"). Multi-tenancy =
one repo per project. Permissions = git permissions. Conflicts = git
merge conflicts. Auditability = git log.

This task authors the Phase 3 ADRs ONLY (decisions before code), mirroring
the Phase 2 pattern (umbrella ADR-0011 + feature ADRs 0012-0017).
Implementation is a SEPARATE subsequent /task once these ADRs are Accepted.

## Pre-flight observations (grounded this session)

- Current branch points at the same commit as `main` (`72331c6`, the Phase
  2 merge). Working tree clean except this task's untracked files. The loop
  authors on a new `phase-3-adrs` branch off `main`.
- `pyproject.toml` already declares `name = "praxis"`, `version = "0.0.1"`,
  CLI entry `praxis = "praxis.cli:main"`, build backend hatchling, src
  layout, optional extras `browser-use` and `live` (anthropic). The
  packaging ADR refines an existing pyproject, not a greenfield one.
- **PyPI name `praxis` is TAKEN** (the JSON API returns HTTP 200).
  `praxis-qa` is free (404). The import name `praxis` and the CLI command
  `praxis` can stay; only the published distribution name must change.
- The package is pure Python: one universal wheel, no platform-specific
  builds. ADR-0020 records this rather than the handoff's per-platform
  assumption.
- CLI today (`src/praxis/cli/main.py`, ~20KB) exposes `init / learn /
  regress / explore / review / status`. Phase 3 reframes the agentic ones
  as a dual surface and adds a teach skill, an interaction with no
  precedent in Phase 0-2.
- `docs/07-roadmap.md` (lines 33-37) and `AGENTS.md` (lines 80-82) BOTH
  still frame Phase 3 as the trust-and-product layer with hosted shared
  memory and monetization. The umbrella ADR reframes these; the accept step
  propagates the reframe into both docs.
- ADR-0011 section 4 DEFERRED governance/RBAC/hosted-multi-tenant/
  dashboards/web-UI/pricing to Phase 3. The reframe does not inherit these
  as deferred; it REPLACES the hosted versions with git-native equivalents
  (one repo per project, git permissions, git log). ADR-0018 records that
  so no later ADR silently revives the SaaS path.

## Steps

- [ ] Step 1: Author ADR-0018 (Phase 3 scope + the library-plus-git
  reframe; umbrella, mirror of ADR-0011/0009). States the no-SaaS reframe
  and its two halves (git as shared memory, Claude Code as local brain),
  names the seven owned items each with its owning ADR (0019-0025),
  REPLACES the ADR-0011 Phase 3 SaaS deferrals with git-native
  equivalents, maps the git layout onto the believed/contested model
  (`.praxis/knowledge/` = seeded/believed; `.praxis/candidates/` =
  contested; promotion = a human seed event via git merge, preserving
  ADR-0005 + ADR-0001), and records that the Phase 1.5 items (Stagehand,
  paid API-key run, auditor `refuted`-status) stay deferred and are NOT
  absorbed into Phase 3.
  - Files: `docs/adr/0018-phase-3-scope-and-library-git-reframe.md`
  - Verification: file exists; `Status: Proposed`; Context cites the
    2026-06-08 reframe and the ADR-0011 section 4 deferrals it supersedes;
    Decision names all seven items with owning ADRs 0019-0025; a subsection
    reframes hosted-multi-tenant into one-repo-per-project, RBAC into git
    permissions, dashboards/web-UI into git log plus markdown reports,
    pricing/GTM into dropped; a subsection maps `.praxis/knowledge` vs
    `.praxis/candidates` onto believed vs contested and states promotion =
    a human seed event via git merge; Forbidden alternatives (no reviving
    hosted SaaS, no hosted multi-tenant backend, no last-write-wins on
    shared knowledge, no absorbing Phase 1.5 items); Consequences names
    invariants respected and explicitly NOT covered (deferred to
    0019-0025); Relation to prior ADRs (Mirror of ADR-0011, supersedes the
    Phase 3 framing in docs/07 + AGENTS.md, builds on ADR-0010 verdict); no
    implementation code; `bash verify.sh` ALL GREEN.

- [ ] Step 2: Author ADR-0019 (brain-pluggability and execution surfaces;
  NEW foundational ADR). Praxis the library is the body (browser control,
  the knowledge store, the deterministic CLI); the brain is pluggable.
  Names the deterministic-vs-agentic split (init/status/review are plain
  CLI and need no brain; teach/regress/explore reason and need one). Names
  the two brains: local = Claude Code via skills (no API key, runs on the
  user's subscription), CI = an API-key agent (the `live` extra). Names the
  dual surface for the agentic operations (a Claude Code skill for the
  local free path, a console CLI for the CI / scriptable path), and that
  the teach operation is the exception that is skill-only because it is
  always human-in-the-loop. Keeps the core runtime-agnostic AND
  brain-agnostic, extending ADR-0003.
  - Files: `docs/adr/0019-brain-pluggability-and-execution-surfaces.md`
  - Verification: file exists; `Status: Proposed`; Context cites the
    no-API-key-locally constraint and the existing two-adapter / `live`
    extra precedent; Decision names the body-vs-brain split, the
    deterministic-vs-agentic operation classes, the two brains and their
    cost model, the dual surface, and the teach-is-skill-only exception;
    Forbidden alternatives (no forcing an API key for local use, no baking
    a single brain into the core, no making the core depend on Claude Code,
    no persisting brain choice into knowledge); Consequences names
    invariants respected (runtime-agnostic-core extended to brain-agnostic,
    adapter-spi-tiny-and-stable, loud-and-traceable-over-silent-and-
    convenient) and NOT covered (packaging mechanics owned by 0020,
    per-operation behavior owned by 0022/0023/0024); Relation (Extends
    ADR-0003, depends on ADR-0018 scope); no implementation code; verify.sh
    ALL GREEN.

- [ ] Step 3: Author ADR-0020 (PyPI packaging and distribution).
  Distribution name `praxis-qa`, import name and CLI command stay `praxis`;
  pure-Python single universal wheel; optional extras keep the core
  runtime-agnostic (ADR-0003) and brain-agnostic (ADR-0019); the `live`
  extra carries the API-key agent for the CI brain; semver with a pre-1.0
  (0.x) schema-may-break window and post-1.0 `schema_version` stability;
  the JSON schema ships as package data inside the wheel; the Claude Code
  skills ship with the package and `praxis init` scaffolds them into the
  project's `.claude/skills/`; the public API surface (adapter SPI, the
  pydantic model, the CLI) is the stable contract.
  - Files: `docs/adr/0020-pypi-packaging-and-distribution.md`
  - Verification: file exists; `Status: Proposed`; Context cites the
    existing pyproject and the `praxis`-taken-on-PyPI fact; Decision states
    the distribution-name decision, the universal-wheel fact, the
    extras-keep-core-clean rule, the `live`-extra-is-the-CI-brain note, the
    versioning policy, schema-as-package-data, the skill-distribution
    mechanism via `praxis init`, and the public-API surface; Forbidden
    alternatives (no pulling browser or API deps into core install, no
    platform-specific wheels, no adding deps beyond pydantic/pyyaml +
    declared extras without a new ADR); Consequences names invariants
    respected (runtime-agnostic-core, adapter-spi-tiny-and-stable,
    schema-is-single-source-of-truth) and NOT covered (the git/.praxis
    layout owned by 0021); Relation (Refines ADR-0003, depends on ADR-0002
    + ADR-0019); no implementation code; verify.sh ALL GREEN.

- [ ] Step 4: Author ADR-0021 (the `.praxis/` repository convention; git as
  shared memory). Directory layout (`config.yaml`,
  `knowledge/<goal>.knowledge.yaml`, `candidates/<goal>/<id>.yaml`,
  `runs/<timestamp>/`, `.praxisignore`); committed vs gitignored (knowledge
  + candidates + config committed/shared; `runs/` raw event logs gitignored
  by default, local and regenerable); how the per-machine append-only event
  log (ADR-0001) relates to the shared git record (git history of
  `.praxis/` IS the team-level append-only analog; force-push to `.praxis/`
  is the forbidden mutation); candidate files are one-file-per-id so
  concurrent adds never merge-conflict (ADR-0012 file-per-event layout is
  load-bearing); `.praxis init` scaffolds the tree, gitignores `runs/`, and
  installs the skills (cross-ref ADR-0020).
  - Files: `docs/adr/0021-praxis-directory-convention.md`
  - Verification: file exists; `Status: Proposed`; Context cites the
    2026-06-08 `.praxis/` naming decision and ADR-0012 file-per-event
    layout; Decision names the full directory layout, the committed-vs-
    gitignored split, the local-event-log-vs-git-history reconciliation,
    the one-file-per-candidate anti-conflict rule, and `.praxis init`
    behavior; Forbidden alternatives (no force-push history rewrite of
    `.praxis/`, no last-write-wins on knowledge, no single mutable
    candidates file, no committing raw run logs by default, no secrets in
    any committed file); Consequences names invariants respected
    (append-only-store-no-mutation, concurrent-writes-lose-no-knowledge,
    no-secrets-tokens-pii-in-knowledge, tenant-scoping-prevents-leakage via
    one-repo-per-project, loud-and-traceable-over-silent-and-convenient)
    and NOT covered (teach/regress/action behavior owned by 0022/0023/0024);
    Relation (Depends on ADR-0001 + ADR-0012, refines the ADR-0011
    tenant-scoping placeholder into one-repo-per-project); no implementation
    code; verify.sh ALL GREEN.

- [ ] Step 5: Author ADR-0022 (`praxis teach` as a Claude Code skill; the
  central Phase 3 ADR). The teach operation is delivered as a skill
  (`/praxis:teach`), not a bare CLI command, because it is always
  human-in-the-loop. Given a NL intent, the agent (the local Claude Code
  brain per ADR-0019) opens the app via the Playwright adapter, explores
  until it can perform the happy path, asks the user interactively when
  blocked, and emits a goal YAML the user reviews before commit.
  Interactive prompt protocol (typed questions: credential / navigation-
  hint / role / confirmation); end condition (happy-path observed AND user
  confirms, with a budget plus wall-time backstop); teach output is
  HUMAN-SEEDED knowledge (the legitimate ADR-0005 first-oracle seed path);
  credentials typed during a teach session drive the browser but are NEVER
  persisted (auth_state from ADR-0017 records authenticated + scope, not
  the secret); a teach session refuses to silently overwrite a believed
  goal, offering a candidate refinement instead.
  - Files: `docs/adr/0022-praxis-teach-skill.md`
  - Verification: file exists; `Status: Proposed`; Context names the teach
    operation as new-behavior-without-precedent, the non-engineer use case,
    and why it is a skill not a command (ADR-0019); Decision specifies the
    skill-not-command delivery, the interactive prompt protocol, the end
    condition, the teach-output-is-seeded-knowledge framing tied to
    ADR-0005, the credentials-never-persisted rule tied to ADR-0017, and
    the no-silent-overwrite-of-believed-goals rule; Forbidden alternatives
    (no persisting credentials/cookies/PII, no writing teach steps as
    click-by-click procedure, no auto-promotion past a human confirm, no
    overwriting a believed goal in place, no shipping the teach operation
    as an autonomous CLI command); Consequences names invariants respected
    (first-oracle-must-be-seeded, provenance-and-confidence-mandatory,
    no-secrets-tokens-pii-in-knowledge, knowledge-not-mbt-procedure-cache,
    invariants-not-coordinates-hierarchy, loud-and-traceable-over-silent-
    and-convenient) and NOT covered (regress aggregation owned by 0023, CI
    automation owned by 0024); Relation (Depends on ADR-0019 brain surface,
    ADR-0021 for where output lands, ADR-0017 for auth_state, ADR-0005 for
    the seed rule, ADR-0003 for the adapter boundary); no implementation
    code; verify.sh ALL GREEN.

- [ ] Step 6: Author ADR-0023 (`praxis regress` and `praxis explore`: dual
  surface + aggregate default + break-vs-drift report). Both operations
  have a console CLI (test-style, what CI runs and what you script) and a
  Claude Code skill (same engine, plus Claude triaging on failure). With no
  `--goal`, the regress operation runs every goal in `.praxis/knowledge/`
  and emits an aggregated non-engineer report; the per-goal verdict is OK /
  REGRESSED (the app broke, a real bug) / STALE (the app changed on
  purpose, the knowledge is outdated); a regression is LOUD (non-zero exit,
  named goal, the signal that flipped); the skill surface adds the
  break-vs-drift triage and proposes the next step (file a bug vs update the
  knowledge); R-mode still excludes auditor scenarios as inputs (ADR-0009
  leak path stays closed); per-goal budget allocation.
  - Files: `docs/adr/0023-regress-explore-dual-surface-and-report.md`
  - Verification: file exists; `Status: Proposed`; Context cites the
    current `--goal`-required runner, the non-engineer ergonomic goal, and
    the dual surface from ADR-0019; Decision specifies the dual surface for
    both regress and explore, the default-all behavior, the OK/REGRESSED/
    STALE report contract with the break-vs-drift distinction, the loud-
    failure contract, the skill-adds-triage behavior, the no-auditor-input
    inheritance, and the per-goal budget rule; Forbidden alternatives (no
    auditor scenarios as regress input, no silent skip of a goal that
    errors, no aggregate "mostly green" that hides a single regression, no
    auto-mutating knowledge on a STALE verdict without a human confirm);
    Consequences names invariants respected (no-silent-success-when-app-
    broken, loud-and-traceable-over-silent-and-convenient, operational-
    knowledge-not-procedures, append-only-store-no-mutation for the
    knowledge-update path) and NOT covered (CI wiring owned by 0024);
    Relation (Depends on ADR-0019 + ADR-0021, extends the ADR-0009 R-mode
    and E-mode contracts); no implementation code; verify.sh ALL GREEN.

- [ ] Step 7: Author ADR-0024 (GitHub Action `praxis-action`; the CI brain
  path). A reusable action runs `praxis regress` (all goals, console
  surface) on every PR and release tag as a gate, using the API-key brain
  (ADR-0019); on scheduled / labeled E-mode runs it runs `praxis explore`
  and opens a DRAFT PR adding the new candidate files under
  `.praxis/candidates/`; promotion is the human reviewing and merging that
  PR (git PR review IS the `praxis review` promotion step, and a merge that
  moves a candidate into `.praxis/knowledge/` is the human seed event of
  ADR-0005); the teach operation never runs in CI (it needs a human); the
  action never writes secrets into the repo or logs and never force-pushes.
  - Files: `docs/adr/0024-github-action-regress-and-candidate-pr.md`
  - Verification: file exists; `Status: Proposed`; Context cites the
    team-shares-knowledge-via-git loop and the CI brain from ADR-0019;
    Decision specifies the R-mode gate trigger, the API-key brain, the
    E-mode draft-PR-of-candidates behavior, the promotion=merge=seed-event
    mapping, the teach-never-in-CI rule, and the no-secrets / no-force-push
    rules; Forbidden alternatives (no auto-merge of candidate PRs, no
    promotion without human merge, no writing secrets into Action logs or
    the repo, no force-push of `.praxis/`, no running the teach operation in
    CI); Consequences names invariants respected (append-only-store-no-
    mutation, first-oracle-must-be-seeded, no-self-corroboration-source-
    independence, no-secrets-tokens-pii-in-knowledge, loud-and-traceable-
    over-silent-and-convenient) and NOT covered (the docs site owned by
    0025); Relation (Depends on ADR-0019 + ADR-0021 + ADR-0023, realizes
    the ADR-0014 `praxis review` promotion via git); no implementation code;
    verify.sh ALL GREEN.

- [ ] Step 8: Author ADR-0025 (landing page + docs site; minimal). The
  story for a non-engineer evaluating Praxis; examples (testapp, Conduit,
  one real OSS app); explicitly NO analytics, NO signup, NO SaaS funnel
  (reinforces the reframe); static-site tooling decision (recommend
  mkdocs-material published via GitHub Pages from `docs/`, lowest-friction
  given docs already live there); the only quantitative claim allowed is
  the ADR-0010 Phase 1 number.
  - Files: `docs/adr/0025-landing-page-and-docs-site.md`
  - Verification: file exists; `Status: Proposed`; Context cites the
    non-engineer evaluation story and the no-SaaS posture; Decision names
    the audience, the example set, the explicit no-analytics/no-signup
    rule, the static-site tooling pick, and the honest-claims rule;
    Forbidden alternatives (no analytics, no signup flow, no hosted-SaaS
    funnel, no marketing claims beyond the validated Phase 1 numbers);
    Consequences names invariants respected (loud-and-traceable-over-
    silent-and-convenient applied to honest claims, operational-knowledge-
    not-procedures in the pitch) and NOT covered (none new); Relation
    (Depends on ADR-0018 reframe, cites ADR-0010 for the only quantitative
    claim allowed); no implementation code; verify.sh ALL GREEN.

- [ ] Step 9: Accept pass + doc propagation (mirrors Phase 2's "Accept ADRs
  0011-0017" commit). Flip ADR-0018 through ADR-0025 from `Proposed` to
  `Accepted (2026-06-08)`; add eight rows to `docs/adr/README.md`
  (two-column format, ` (Accepted)` inline); rewrite the Phase 3 section of
  `docs/07-roadmap.md` and the Phase 3 paragraph of `AGENTS.md` to the
  library-plus-git reframe; add a CHANGELOG.md entry for "Phase 3 (ADRs
  0018 through 0025)".
  - Files: `docs/adr/0018-*.md` through `docs/adr/0025-*.md` (status line),
    `docs/adr/README.md`, `docs/07-roadmap.md`, `AGENTS.md`, `CHANGELOG.md`
  - Verification: all eight ADR status lines read `Accepted`; README has
    eight new rows after ADR-0017; `docs/07-roadmap.md` Phase 3 section no
    longer says hosted shared memory or monetization; `AGENTS.md` Phase 3
    paragraph reflects library-plus-git; CHANGELOG has the Phase 3 entry;
    `bash verify.sh` ALL GREEN.

## Pre-conditions

- Branch: `phase-3-adrs` off `main`.
- Baseline: `bash verify.sh` ALL GREEN (confirmed this session, exit 0).
- Sealed artifacts the loop MUST NOT touch: everything under `src/praxis/`,
  `schema/`, `experiments/`, and the Phase 1 sealed run dirs. This task
  writes ONLY under `docs/adr/`, `docs/07-roadmap.md`, `AGENTS.md`,
  `CHANGELOG.md`, and `tasks/phase-3-adrs/`. No code, no schema, no
  pyproject change (the packaging decision is recorded; pyproject edits are
  Phase 3 implementation, a later /task).

## Risks / unknowns

- **PyPI name collision.** `praxis` is taken; the dist name is `praxis-qa`.
  Pursuing the literal `praxis` would need a PyPI dispute outside this repo.
- **Git-as-event-log tension.** ADR-0001 says the append-only event log is
  the source of truth; git adds a second history layer. ADR-0021 resolves
  it (local event log stays per-machine source of truth; git history of the
  committed projection is the shared append-only analog; force-push
  forbidden).
- **The teach operation is genuinely new.** Interactive prompt/reply has no
  precedent. ADR-0022 specifies the protocol; the implementation risk
  (driving a real browser while blocking on user input) lands in the later
  implementation /task.
- **Skill distribution is novel for a pip package.** Shipping Claude Code
  skills via a wheel and scaffolding them with `praxis init` is unusual;
  ADR-0020 records the mechanism, the implementation /task proves it.
- **This task records decisions only.** Every ADR describes behavior the
  implementation /task must build; none is enforced by code yet.

## Execution note (ultracode)

The loop is realized as a Workflow: author the umbrella (0018) then the
brain ADR (0019) sequentially, fan out 0020-0025 in parallel each given the
0018 + 0019 content plus the house style and contract, then adversarially
verify each ADR against the AGENTS.md five non-negotiables, the no-SaaS
reframe, correct cross-references, ASCII-only output, and house-style
shape. The main loop writes each file and commits per step (one commit per
ADR, then the accept commit). Push / merge to `main` is gated on Pablo's
explicit approval after the loop (the repo forbids push without it).
