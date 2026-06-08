# 02 — Architecture

## Separation of concerns
Six components, with a hard line between the **runtime-agnostic core** and the
**runtime-specific adapters**.

```
                          ┌─────────────────────────────────────────┐
   runtime (Browser Use,  │                 ADAPTERS                 │
   Stagehand, Playwright) │  read_knowledge(goal) / write_obs(...)   │
        ▲   │             └───────────────┬──────────────▲──────────┘
        │   │ regenerate steps            │ believed      │ observation
        │   ▼                             ▼ knowledge      │ events
   ┌────┴─────────┐   project   ┌─────────────────┐   append   ┌──────────────┐
   │   ORACLE     │◀────────────│      MERGE      │◀───────────│    STORE     │
   │ trust gate   │  validate   │ projection /    │   events   │ append-only  │
   │ on signals   │────────────▶│ truth engine    │            │ event log    │
   └──────────────┘             └────────┬────────┘            └──────────────┘
                                         │ typed by
                                ┌────────▼────────┐
                                │      MODEL      │  <- schema/knowledge.schema.json
                                └─────────────────┘
```

### 1. Model (`src/praxis/model`)
Typed representation of a knowledge entry, mirroring
`schema/knowledge.schema.json`. Validates that every assertion carries
provenance + confidence. Round-trips YAML ↔ objects.

### 2. Store (`src/praxis/store`) — source of truth
Append-only event log. Each agent observation is one immutable event (one file
per event for lock-free concurrency, CORAL-style). No update, no delete.
Backend is pluggable: `FileEventStore` for the MVP → SQLite → Postgres + pgvector
at scale. See ADR-0001.

### 3. Merge (`src/praxis/merge`) — truth engine
Folds events into the *believed* knowledge state: aggregates repeated
observations into confidence, marks each assertion `believed` / `contested` /
`stale` / `quarantined`, applies recency decay, and **preserves contradictions
rather than silently choosing a winner.** This is where multi-agent conflict
resolution lives (docs/05).

### 4. Oracle (`src/praxis/oracle`) — the guardrail
Scores success/failure signals for trustworthiness using independence,
redundancy, recency, and agreement. Gates promotion to `believed` (≥2
independent sources). Quarantines flip-flopping signals. This is the hardest and
most important module; the product is really a trust layer (docs/06).

### 5. Adapters (`src/praxis/adapters`) — the only runtime code
Two responsibilities: hydrate an agent with believed knowledge for a goal, and
translate observations back into store events. Redact secrets/PII at this
boundary. Adapters are optional install extras. See ADR-0003.

### 6. Governance (Phase 3, not yet a package)
Provenance dashboards, access control, secret redaction policy, poisoning
detection, retention/decay policy, hosted shared memory. The product layer.

## Data flow (one run)
1. Adapter calls `read_knowledge(goal_id)` → merge returns the believed projection.
2. Agent attempts the goal, **regenerating its own steps** from intents + signals.
3. Agent emits observations (states seen, signals matched, what worked, what was
   uncertain) → adapter → `store.append(event)`.
4. Merge updates the projection; oracle re-evaluates affected signals.

## Interop model
- The **schema** is the neutral interchange format (ADR-0002) — describes goals
  and states, never imperative actions.
- **MCP** is the transport when knowledge is shared across processes/agents
  (an MCP memory server exposing the store is the natural Phase 2/3 surface).
- **Adapters** are the per-runtime bridge. Two runtimes may regenerate different
  steps from identical knowledge; that is correct, because the procedure is disposable.

## Scaling axes (design for these from day one)
| Axis | MVP | At scale |
|------|-----|----------|
| Storage | one JSON file per event | Postgres + pgvector, snapshotted projections |
| Concurrency | file-per-event, no locks | same model; partition by goal_id/project |
| Org structure | one local store | read-only shared store + per-session read-write (Claude-memory pattern) |
| Schema evolution | `schema_version: "0"` | versioned migrations, never break old events |
| Runtime breadth | 1 adapter | adapter SPI, community adapters |
| Trust | observation_count + independence | full provenance graph, poisoning detection |
