# Changelog

## 2026-06-08: Phase 2 (ADRs 0011 through 0017)

Phase 1 ended on 2026-06-07 with verdict CONTINUE: the memory arm beat the steelmanned cold-readme baseline on every pre-registered gate, so the operational-knowledge moat survives ([ADR-0010](docs/adr/0010-phase-1-regression-recall-verdict.md)). Phase 2 is the follow-up. It widens the contract from one agent and a toy app to many agents writing concurrently against a real public app, with knowledge that ages out, hunches that persist, and a hidden score that says whether exploration is paying for its tokens.

Scope sealed in [ADR-0011](docs/adr/0011-phase-2-scope-and-deferrals.md). Five features ship, one ADR each:

- **Multi-writer concurrency** ([ADR-0012](docs/adr/0012-multi-writer-concurrency-contract.md)). Many agents append to the same shared memory at once, no notes lost, no identical agents faking agreement.
- **Recency decay** ([ADR-0013](docs/adr/0013-recency-decay-as-projection-derivation.md)). Knowledge ages out when no agent re-confirms it within N app versions or T days; every demotion writes a visible audit event.
- **Candidate persistence** ([ADR-0014](docs/adr/0014-e-mode-candidate-persistence.md)). Exploring-agent hunches survive across runs as "contested" until a human or an independent agent agrees.
- **Exploration reward** ([ADR-0015](docs/adr/0015-exploration-reward-pre-registration.md)). One hidden number per run that scores useful new knowledge per token spent; the agent never sees it.
- **Real-app SUT: Conduit + `auth_state`** ([ADR-0016](docs/adr/0016-real-app-sut-selection.md), [ADR-0017](docs/adr/0017-schema-extension-auth-state.md)). Experiment moves onto Conduit (a public Medium-clone) and the schema gains an `auth_state` field that records login posture without storing credentials.

Index doc: [docs/phase-2-features/README.md](docs/phase-2-features/README.md).
