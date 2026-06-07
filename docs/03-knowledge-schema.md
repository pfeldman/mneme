# 03 — Knowledge schema

Two schemas live in `schema/`:
- `knowledge.schema.json` — the **active Phase-0 schema** (minimal).
- `knowledge.phase1.schema.json` — the **richer target, reference only**, not
  used until the thesis is validated (states, paths-as-graph, risks, uncertainties).

Trimming the active schema is deliberate: a rich schema nobody fills correctly is
a failure mode (schema rot, docs/06). Grow it from real need, after Phase 0.

## Guiding rule
**Store invariants, not coordinates.** If a human redesigning the UI would
invalidate a value, it is not durable knowledge.

## Phase-0 fields (active)
- `goal_id` / `goal` — stable id + intent as a **capability** ("a user can
  authenticate"), not a UI action.
- `target` — app, environment, `observed_app_versions` (to detect stale knowledge
  after a release).
- `success_signals` / `failure_signals` — the oracles.
- `meta` — timestamps, contributing agents.

## Signals (`$defs/signal`)
`type` is ordered most→least durable:
`behavioral` → `network` → `accessibility` → `text` → `url` → `visual`.
Selectors/XPath/coordinates are deliberately not representable. **The oracle
treats DIFFERENT types as independent evidence; same-type repeats are not.**

## Provenance + confidence (mandatory, ADR-0004 + ADR-0005)
Every signal carries `provenance` and a 0–1 `confidence` and a `status`
(`believed` / `contested` / `stale` / `quarantined`). Provenance:
- `source_type`: `human` | `spec` | `agent`. **human/spec = a seeded oracle,
  trusted from cold-start.** `agent` = self-observed, needs evidence diversity to
  become `believed`.
- `source_id`: agent id, person, or spec reference (e.g. an acceptance-criteria id).
- `observed_app_version`, `last_verified`, `observation_count` (raises confidence
  within the signal; does NOT create independence).

The naive `independent_sources` counter from the first draft was removed (ADR-0005):
two runs of the same model are not independent.

## How an oracle becomes trustworthy
`believed` iff a seeded (human/spec) signal exists OR ≥2 signals of different
`type` agree. See `src/praxis/oracle` and ADR-0005. The login example shows both.

## Phase-1 extensions (reference schema only)
- `states` — semantic state identity via redundant recognition signals.
- `paths` — alternative routes as a graph of intents (enables goal composition,
  e.g. checkout reusing authenticate as a precondition).
- `risks` — conditional hazards, each with a **trigger**.
- `uncertainties` — open questions that drive exploration.
Do not implement these in Phase 0.

## What the schema forbids (both phases)
Selectors/XPath/coordinates as durable knowledge; steps/timings; run-specific
data (tokens, generated IDs, fixtures, counts — also a leakage risk, docs/06);
any assertion without provenance + confidence.
