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

## Checkable signals: `value_predicate` (ADR-0030)
A signal's `value` is human-readable prose; by default the matcher compares an
observation to it by word-overlap (Jaccard) above a floor. That conflates two
things in one string: the INVARIANT (the durable fact: a create endpoint
returns 2xx; the route is the editor for the just-created campaign) and the
per-run INSTANCE (the campaign id, the exact hostname) that changes every run.
An honest run that reports the real instance instead of the seed's placeholder
reads as "different prose" and the match wrongly fails.

A signal can OPT IN to a checkable form with an optional `value_predicate`: a
template string that is HARD on the invariant and TOLERANT only on declared
per-run instance tokens. It SUPPLEMENTS `value` (which stays required and is the
projection grouping key); it does not replace it.

- Text OUTSIDE a `{slot}` is the INVARIANT, matched EXACTLY (case-folded and
  whitespace-normalized only; punctuation is NOT normalized away).
- A `{slot}` marks a per-run instance token the matcher tolerates on PRESENCE
  only (it must be filled by a non-empty token; its literal value is never
  compared between the seed and the run).
- A slot may declare a SHAPE the filler must satisfy: `{slot:numeric}` (all
  digits) or `{slot:uuid}` (a UUID shape). A bare `{slot}` is presence-only.
  These three are the whole vocabulary; richer shapes are a future ADR.

Example (the worked `create-welcome-popup` case):
`the route matches /Box/Editor/{campaign_id:numeric}` - the route prefix is the
invariant, the numeric campaign id is the slot. A non-numeric segment, a wrong
route, or a missing id is a NON-match.

The structured path is STRICTER than Jaccard, never looser (ADR-0030 decision
3): a `returns 500` observation cannot satisfy a `returns 2xx` invariant, where
word-overlap could have admitted it. A predicate with no invariant (only a
slot, or only stopwords) or a malformed slot is REJECTED LOUDLY at write time,
never silently downgraded to the free-text path. The free-text path is
unchanged for any signal that leaves `value_predicate` unset (decision 4); the
two paths coexist. The slot convention mirrors the inline `{username}` / `{tag}`
slots the `risks.trigger.expect` predicate already uses.

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
