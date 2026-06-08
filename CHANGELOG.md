# Changelog

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
