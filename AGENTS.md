# AGENTS.md - build brief for Claude Code

You are building **Praxis** (formerly `mneme`; renamed in ADR-0009), a
shared **operational-knowledge** layer for QA agents. This file is your
contract. Read it fully before writing code, then read
`docs/01-vision-and-thesis.md`, `docs/02-architecture.md`,
`docs/04-mvp-experiment.md`, `docs/phase-0-results.md`,
`docs/adr/0009-phase-1-scope-and-praxis-reframe.md`,
`docs/phase-1-plan.md`, `docs/phase-1-experiment.md`,
and `schema/knowledge.schema.json`.

## What you are building (and what you are NOT)
You ARE building an **operational-knowledge** layer: agents store and
maintain knowledge about a system under test (success/failure oracles,
risks, uncertainties), decoupled from the procedure used to reach a goal.
The Phase 1 reframe (ADR-0009) calls this out explicitly: Phase 0
measured "knowing what success means saves tokens", not "memory wins".
The durable claim is that operational knowledge - what counts as
success, what is risky, what is unknown - is what survives a strong
cold agent navigating happy paths cheaply.

You are NOT building a test runner, a selector engine, or a recorder. If you
catch yourself persisting click-by-click steps as the source of truth, STOP -
that is the failure mode this project exists to avoid. The procedure is
disposable; the knowledge is the asset.

## The five non-negotiables
1. **Invariants, not coordinates.** Never make raw CSS selectors, XPath, or
   coordinates first-class durable knowledge. Prefer, in order:
   behavioral → network → accessibility/role → text → url → visual.
2. **Provenance + confidence are mandatory** on every assertion (ADR-0004).
   Validation rejects entries without them.
3. **Append-only store** (ADR-0001). Knowledge is never mutated or deleted.
   Believed state is a *projection* over an immutable event log.
4. **Runtime-agnostic core** (ADR-0003). `model`, `store`, `merge`, `oracle`
   have ZERO runtime dependencies and are testable without a browser. Runtime
   code lives in `adapters/` as optional extras.
5. **The oracle is sacred** (ADR-0005, docs/06). A success oracle is `believed`
   only via evidence DIVERSITY (≥2 signals of *different* type agree) OR a
   human/spec SEED — never by counting agents (two runs of the same model fail
   identically). The first oracle for a goal is **seeded, not self-certified**.
   A confidently-wrong oracle is worse than no memory.

## Build order - do not skip ahead

**Phase 0 - validated and closed (2026-06-07).** UI-mutation experiment
under `experiments/ui-mutation/` cleared all three gates with margin
(see `docs/phase-0-results.md`, ADR-0007). What Phase 0 actually measured
is "knowing what success means saves tokens" - narrower than the early
framing claimed (ADR-0009). Do NOT rebuild Phase 0; it is the baseline.

**Phase 1 - operational-knowledge layer + regression-recall falsifier
(IN PROGRESS).** ADR-0009 scopes Phase 1 deliberately:
- Activate ONLY the Phase-1 schema fields the regression-recall
  experiment consumes: `risks` (with structured triggers) and
  `uncertainties`. Leave `states` and `paths` deferred. `refuted`
  status was rejected (violated ADR-0008 source-independence).
- Ship `src/praxis/runner/` with two modes:
  - R-mode (`regression.py`): pre-deploy check. Reads believed
    success/failure signals, computes pass/fail per goal. Auditor
    scenarios are NOT an input (leak path closed).
  - E-mode (`exploration.py`): hunts off-happy-path. Reads risks +
    uncertainties + failure-signal watch-list. Logs
    `off_path_fraction` so the experiment's hard kill criterion
    (>= 0.4) can fire if E-mode collapses into R-mode.
- CLI (`src/praxis/cli/`): `init / learn / regress / explore /
  review / status`. Stdlib argparse only.
- `experiments/regression_recall/`: planted-regression manifest +
  metrics with sigma-bounded kill gates + arm harness + frozen
  pre-registration artifacts. Subscription-path protocol in
  `LOCAL_RUN.md`.
- Stagehand adapter and auditor-as-input are DEFERRED to Phase 1.5.

**Phase 2 - multi-agent.** Concurrent writers, contradiction detection,
recency decay, an explicit **exploration incentive** so agents don't
all converge on the happy path and silently shrink coverage (docs/05,
docs/06). Also: real-app generalization (move off `testapp.py`),
`refuted` status + auditor-as-input revisited with proper diversity.

**Phase 3 - trust/product layer.** Governance, secret redaction,
provenance dashboards, poisoning detection, hosted shared memory,
retention policy. The moat.

## Conventions
- Python 3.11+, pydantic v2 for the model, `ruff` + `mypy` clean, `pytest`.
- Schema is the single source of truth for shape; the pydantic model mirrors it,
  with a test asserting they agree.
- Every structural decision gets an ADR in `docs/adr/`.
- Keep the adapter SPI tiny and stable (`read_knowledge`, `write_observations`).
- Never write secrets, tokens, generated IDs, or PII into knowledge/events
  (docs/06 leakage). Redact at the adapter boundary.
- Ask before adding any dependency beyond pydantic/pyyaml + the Browser Use extra.

## Definition of done for any change
- `schema/examples/*.knowledge.yaml` still validate against the active
  schema (`schema/knowledge.schema.json` - Phase-1 since ADR-0009).
- New assertion fields propagate provenance + confidence (signals and
  risks) or author + timestamp (uncertainties).
- Tests pass; `ruff` and `mypy` clean; `bash verify.sh` ends ALL GREEN.
- If you touched store/projection, a test proves two agents' concurrent
  writes do not lose knowledge.
- If you touched the Phase-1 schema, the model<->schema agreement test
  catches drift.

## How to think when stuck
The hardest module is `oracle/`, not `store/`. Storage is easy; deciding what is
*true* across fallible agents is the whole product. When unsure, choose the
option that makes a wrong assertion **loud and traceable** over silent and convenient.
