# AGENTS.md — build brief for Claude Code

You are building **Mneme**, a shared semantic-memory layer for QA agents. This
file is your contract. Read it fully before writing code, then read
`docs/01-vision-and-thesis.md`, `docs/02-architecture.md`, `docs/04-mvp-experiment.md`,
and `schema/knowledge.schema.json`.

## What you are building (and what you are NOT)
You ARE building a memory/knowledge layer: agents store and maintain knowledge
about a system under test, decoupled from the procedure used to reach a goal.

You are NOT building a test runner, a selector engine, or a recorder. If you
catch yourself persisting click-by-click steps as the source of truth, STOP —
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

## Build order — do not skip ahead
**Phase 0 — validate the thesis (`experiments/ui-mutation/`). Build this FIRST.**
Nothing else matters until the experiment clears its gates. Measurement order is
deliberate: the **existential gate (memory vs a COLD agent on cost/reliability)
runs FIRST** — if a cold agent already wins, the layer has no reason to exist
(docs/06). Robustness vs a recorded script is Measurement 2.

Minimum to run Phase 0:
- `model/`: load/dump/validate `*.knowledge.yaml` against the **minimal Phase-0
  schema** (`schema/knowledge.schema.json`). Do NOT implement the Phase-1
  reference schema (states/paths/risks/uncertainties).
- `store/`: a `FileEventStore` (one JSON file per observation event).
- `merge/`: `project(events) -> KnowledgeFile` computing confidence + status from
  same-type observation counts + recency. Never last-write-wins.
- `oracle/`: the diversity-or-seed rule above; quarantine flip-floppers.
- `adapters/browser_use.py` (`read_knowledge` / `write_observations`).
- `adapters/playwright.py`: emit a recorded script as the brittle baseline.
- `experiments/ui-mutation/`: fill `metrics.py`, `harness.py`, `mutate.py`;
  run the **existential gate first** and short-circuit if it fails.

**Phase 1 — OSS memory library.** Harden core; activate the richer schema
(`knowledge.phase1.schema.json`: states, paths-as-graph, risks, uncertainties);
add a Stagehand adapter and benchmark Mneme against its action cache; publish docs.

**Phase 2 — multi-agent.** Concurrent writers, contradiction detection, recency
decay, and an explicit **exploration incentive** so agents don't all converge on
the happy path and silently shrink coverage (docs/05, docs/06).

**Phase 3 — trust/product layer.** Governance, secret redaction, provenance
dashboards, poisoning detection, hosted shared memory, retention policy. The moat.

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
- `schema/examples/*.knowledge.yaml` still validate against the Phase-0 schema.
- New assertion fields propagate provenance + confidence.
- Tests pass; `ruff` and `mypy` clean.
- If you touched store/projection, a test proves two agents' concurrent writes
  do not lose knowledge.

## How to think when stuck
The hardest module is `oracle/`, not `store/`. Storage is easy; deciding what is
*true* across fallible agents is the whole product. When unsure, choose the
option that makes a wrong assertion **loud and traceable** over silent and convenient.
