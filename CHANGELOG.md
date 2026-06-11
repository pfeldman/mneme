# Changelog

## 2026-06-11: 0.0.3 (first real-app dogfooding fixes)

Fixes surfaced by the first integration of `praxis-qa` against a real production web app, ordered by release impact:

- **Console runner reuses the saved auth session.** The `claude -p` console brain now loads the saved Playwright storage state via the native `@playwright/mcp --storage-state` argument for an authenticated, non-subject goal (resolved by role through the existing `auth_session` channel, env-secret beats file per ADR-0026), so the console surface stops producing a false REGRESSED on an authenticated goal that the skill surface passed. A `being_tested` login-subject goal still does a real login; a missing session surfaces the loud AUTH-EXPIRED outcome, never a silent logged-out run. The brain preamble now tells the agent the `.praxis.secrets` channel exists.
- **Every verdict-reaching regress run persists an observation record.** A console regress that reached a verdict was persisting zero observation events, so a REGRESSED verdict was not traceable. A new non-promotable regress observation record is now written for every evaluated goal (single-goal and aggregate, console and skill) into a sibling `regress/` store subdir the belief projection never reads, so traceability holds without growing the believed set (ADR-0029 closure intact).
- **Streaming endpoints and network signals.** Teach guidance now warns against typing a fact `network` for a streaming or long-lived endpoint (SSE, chunked, websockets), whose request often has no final status when the regress agent samples the network log, which made a genuinely-passing goal come back UNCERTAIN then falsely REGRESSED. The deeper "observed-but-still-streaming as a typed partial confirmation" option is captured as [ADR-0032](docs/adr/0032-streaming-network-signal-partial-confirmation.md) (Proposed), not shipped, because it touches verdict semantics.
- **Aggregate JUnit XML.** The aggregate (CI) regress path now emits JUnit XML with one testcase per goal, mapped to the same exit-code contract as the single-goal emitter (OK pass, REGRESSED / ERROR / AUTH-EXPIRED failure, STALE skipped); previously only single-goal runs produced JUnit.
- **Docs and packaging honesty.** The example CI workflow documents the `claude -p` CLI brain it actually runs (not the experiment-only `anthropic` extra); the README is retitled Praxis (no longer "Mneme" / "skeleton") with a loud note that a STALE-only run exits 0 so an exit-code-only gate passes drifted knowledge; the mkdocs nav lists the current ADRs; and the Windows launch path routes an npm `.cmd` shim through the command interpreter so a preflight-passing `claude` actually starts.

## 2026-06-08: Phase 3 implementation

The code for the Phase 3 decisions below. The eight ADRs were the spec; this pass built them, in dependency order, on `bash verify.sh` ALL GREEN throughout. What shipped:

- **`praxis-qa` packaging.** The distribution is renamed `praxis-qa` (the import and the `praxis` command stay `praxis`). The schema and a `src/praxis/skills/` tree ship inside the wheel as package data, resolved at runtime by `src/praxis/resources.py` both from an installed wheel and from the source tree.
- **The `.praxis/` convention + init scaffolding.** `praxis init` builds the ADR-0021 tree (`config.yaml`, `knowledge/`, `candidates/`, the gitignored `runs/<timestamp>/` log, a `.praxisignore`), appends `.praxis/runs/` and `.praxis.secrets` to the repo `.gitignore` idempotently, and unpacks the packaged Claude Code skills into `.claude/skills/`.
- **The `.praxis.secrets` channel.** A credentials loader where an environment variable beats the `.praxis.secrets` file, a `MissingCredential` error that names the absent key, and a console helper that fails loudly (non-zero, names the key, no prompt). The file is gitignored before any secret can be written; a secret value is never echoed to stdout or a log.
- **One-file-per-id candidates.** Contested explore candidates are committed as one YAML file per observation event id under `.praxis/candidates/<goal>/`, never a shared mutable list, so concurrent discoveries across machines merge cleanly. Dedup and corroboration stay at the projection, grouped by trigger.
- **The brain-agnostic dual surface.** The regress / explore engine runs from the console CLI and from a skill driver against the same store with the same verdict; the brain is the pluggable seam, and `init` / `status` / `review` need no brain. The core path imports and tests with no LLM present.
- **Default-all `regress` with the OK / REGRESSED / STALE verdict.** With no `--goal`, regress runs every believed goal and emits one aggregate report that tells a real break (REGRESSED) from intentional drift (STALE), each with its evidence. A REGRESSED or ERROR goal fails the run loudly with a non-zero exit and a named goal plus signal; one regression never hides behind a mostly-green roll-up. Per-goal token and wall-time budget slices turn an exhausted slice into a loud ERROR, not a silent skip.
- **Default-all `explore` with the trigger-grouped candidate report.** With no `--goal`, explore hunts off-happy-path across every believed goal, writes candidate files on both surfaces, and reports findings grouped by trigger: each finding once, annotated with its observation count and its DISTINCT source count (N same-`agent_identity` observations are one source, ADR-0008). The `off_path_fraction` floor logging is kept.
- **The teach session seams + the three Claude Code skills.** The teach support module emits a human-seeded goal (mandatory provenance + confidence), records only the abstract `auth_state` and never the credential, refuses to overwrite a believed goal in place (a re-teach appends a contested candidate refinement), and on non-convergence writes no goal and emits a loud not-converged event. The `/praxis:teach`, `/praxis:regress`, and `/praxis:explore` skills ship in package data and scaffold via `praxis init`; their triage is advisory and never mutates committed knowledge.
- **The documented example CI workflow.** One copy-paste GitHub Actions example that runs `praxis regress` as a gate on its exit code. It is explicitly an example the team adapts; Praxis ships no reusable action, never pushes, never opens a PR, never runs teach in CI.
- **The mkdocs docs site.** A `mkdocs.yml` sourced from the existing `docs/` tree with no analytics, no signup, and no funnel; the only CTAs are `pip install praxis-qa` and the repo link. The example set walks through testapp, Conduit, and one real public OSS app, framed as a demonstration, not a benchmark.

A cross-feature integration run (`tests/test_integration_phase3.py`, documented in `tasks/phase-3-impl/integration-run.md`) proves the multi-writer store, recency decay, the committed candidate tree, and the `.praxis/` layout compose on one project with no knowledge lost.

## 2026-06-08: Phase 3 (ADRs 0018 through 0025)

Phase 2 widened the contract to many agents and a real app. Phase 3 turns Praxis from a research repo into a shippable library: `pip install praxis-qa`, with a project's knowledge living under `.praxis/` and shared through git, no SaaS and no hosted backend. Scope and the no-SaaS reframe are sealed in [ADR-0018](docs/adr/0018-phase-3-scope-and-library-git-reframe.md): hosted multi-tenant memory becomes one repo per project, governance becomes git permissions, dashboards become git log, and pricing is dropped.

Eight decisions, one ADR each:

- **Brain pluggability** ([ADR-0019](docs/adr/0019-brain-pluggability-and-execution-surfaces.md)). The library is the body; the reasoning brain is pluggable. Local is Claude Code via skills (no API key, on your subscription), CI is an API-key agent. Deterministic commands (`init`, `status`, `review`) need no brain; agentic ones (`teach`, `regress`, `explore`) do.
- **PyPI packaging** ([ADR-0020](docs/adr/0020-pypi-packaging-and-distribution.md)). Published as `praxis-qa` (the import and the command stay `praxis`), one universal wheel; the schema and the Claude Code skills ship inside it and `praxis init` scaffolds them.
- **The `.praxis/` convention** ([ADR-0021](docs/adr/0021-praxis-directory-convention.md)). Knowledge and contested candidates are committed and shared by git; raw run logs and a `.praxis.secrets` credentials file are gitignored. One file per candidate observation, so concurrent discoveries never merge-conflict; force-push to `.praxis/` is forbidden.
- **`teach` as a skill** ([ADR-0022](docs/adr/0022-praxis-teach-skill.md)). A human-in-the-loop Claude Code skill that learns a goal from a natural-language intent, asks typed questions when blocked, and emits a human-seeded goal. Credentials drive the browser but are never persisted.
- **`regress` and `explore` dual surface** ([ADR-0023](docs/adr/0023-regress-explore-dual-surface-and-report.md)). A console CLI (test-style, what CI runs) plus a Claude Code skill (with triage). The report tells a real bug (REGRESSED) from intentional drift (STALE); candidate findings dedupe by trigger into one corroborated entry, not duplicates.
- **CI integration** ([ADR-0024](docs/adr/0024-ci-integration-invoking-commands.md)). Praxis owns no CI machinery: a team calls the console commands in its own CI and gates on the exit code. Pushing, pull requests, and auth are the team's git; promotion stays a human merge.
- **Landing page and docs site** ([ADR-0025](docs/adr/0025-landing-page-and-docs-site.md)). A minimal non-engineer story with no analytics, no signup, and no SaaS funnel, generated from `docs/` with mkdocs, plus a copy-paste example CI workflow.

These ADRs record decisions; the implementation is a separate follow-up.

## 2026-06-08: Phase 2 (ADRs 0011 through 0017)

Phase 1 ended on 2026-06-07 with verdict CONTINUE: the memory arm beat the steelmanned cold-readme baseline on every pre-registered gate, so the operational-knowledge moat survives ([ADR-0010](docs/adr/0010-phase-1-regression-recall-verdict.md)). Phase 2 is the follow-up. It widens the contract from one agent and a toy app to many agents writing concurrently against a real public app, with knowledge that ages out, hunches that persist, and a hidden score that says whether exploration is paying for its tokens.

Scope sealed in [ADR-0011](docs/adr/0011-phase-2-scope-and-deferrals.md). Five features ship, one ADR each:

- **Multi-writer concurrency** ([ADR-0012](docs/adr/0012-multi-writer-concurrency-contract.md)). Many agents append to the same shared memory at once, no notes lost, no identical agents faking agreement.
- **Recency decay** ([ADR-0013](docs/adr/0013-recency-decay-as-projection-derivation.md)). Knowledge ages out when no agent re-confirms it within N app versions or T days; every demotion writes a visible audit event.
- **Candidate persistence** ([ADR-0014](docs/adr/0014-e-mode-candidate-persistence.md)). Exploring-agent hunches survive across runs as "contested" until a human or an independent agent agrees.
- **Exploration reward** ([ADR-0015](docs/adr/0015-exploration-reward-pre-registration.md)). One hidden number per run that scores useful new knowledge per token spent; the agent never sees it.
- **Real-app SUT: Conduit + `auth_state`** ([ADR-0016](docs/adr/0016-real-app-sut-selection.md), [ADR-0017](docs/adr/0017-schema-extension-auth-state.md)). Experiment moves onto Conduit (a public Medium-clone) and the schema gains an `auth_state` field that records login posture without storing credentials.

Index doc: [docs/phase-2-features/README.md](docs/phase-2-features/README.md).
