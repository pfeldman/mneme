# ADR-0006: Phase-0 status semantics — "uncorroborated" maps to `contested`

Status: Accepted (refines ADR-0004/0005 for the minimal Phase-0 schema)

## Context
The Phase-0 schema's `status` enum has exactly four values:
`believed | contested | stale | quarantined`. ADR-0005 forbids promoting a single
agent-observed evidence type to `believed`. But a signal that is consistent and
fresh yet observed under only ONE evidence type (no diversity, no seed) is not
"believed", not oscillating (`quarantined`), not aged (`stale`), and not strictly a
contradiction either. The four-value enum has no dedicated "hypothesis / not yet
corroborated" state.

## Decision
In Phase 0, `contested` is the single not-yet-trustworthy bucket. A signal is
`contested` when EITHER:
- positive and negative observations disagree (a genuine contradiction), OR
- it is a lone agent-observed type that is consistent but lacks corroboration (a
  different-type signal or a human/spec seed).

Only `believed` success signals form an oracle. `contested` never does. Precedence
when classifying a signal (most→least severe):
`quarantined` > `contested(contradiction)` > `stale` > `believed` > `contested(uncorroborated)`.

This matches the bundled example: a lone, somewhat-aged agent `text` signal is
`contested`, while the success signals are `believed` because two different types
(behavioral + network) plus a spec seed agree.

## Consequences
+ The diversity-or-seed rule (ADR-0005) is enforceable inside the four-value enum
  without inventing schema fields before the thesis is validated (avoids schema rot,
  docs/06).
+ "Not yet trustworthy" is explicit and queryable, not silently `believed`.
- `contested` is overloaded (conflict vs. insufficiency). Phase 1 may split it into
  a distinct `unconfirmed`/`hypothesis` status once the schema is allowed to grow.
