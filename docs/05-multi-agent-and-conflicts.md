> **Read in Phase 2+.** Not needed to run the Phase-0 experiment. Single-writer Phase 0 does not hit these problems.

# 05 — Multi-agent collaboration and conflicts

Multiple agents reading and writing one memory is **technically viable** — CORAL
and the Claude managed-memory pattern both demonstrate it. Storage is the easy
part. The hard parts are below; design for them before turning on a second writer.

## The mechanism (easy part)
Append-only event log, one file per observation (ADR-0001). Concurrent writers
need no locks because each writes a distinct event. The believed state is a
projection (merge). A shared read-only store + per-session read-write store
(Claude-memory pattern) cleanly separates "established truth" from "in-progress
hypotheses".

## The hard parts (design for each)

### Truth decay / staleness
The app changes; memory doesn't. Every assertion has `last_verified` and
`observed_app_version`; the projection lowers confidence with age and flags
assertions not seen under the current version as `stale`. Stale ≠ deleted —
it is demoted, traceably.

### Contradiction and oscillation
Agent A sees a captcha, B never does. Do not let the projection flip-flop.
Represent disagreement explicitly (`contested`) with both observations retained,
and let the oracle/governance decide, rather than last-write-wins.

### Poisoning and cascading error
One wrong high-confidence assertion spreads to every agent. Mitigations:
append-only log makes it traceable and reversible; the oracle requires ≥2
independent sources before `believed`; flip-flopping signals are `quarantined`.
This is the primary failure mode — see docs/06.

### Convergence on a local optimum (the counterintuitive one)
If everyone reuses the known happy path, exploration dies and **coverage shrinks
over time** — shared memory can make your tests progressively blinder. Counter it
with an explicit exploration incentive: reward agents for resolving
`uncertainties` and discovering new `paths`, not just for re-achieving goals.

### Provenance and trust
Every assertion knows who said it, when, under which app version, how often, and
how independently. This is the substrate for all conflict resolution and for the
Phase 3 trust layer.

### Security: shared-state leakage
Writing learned knowledge to a shared store can leak one user's data/secrets to
another (a structural problem, not incidental). Never persist secrets, tokens,
generated IDs, or PII; redact at the adapter boundary; scope stores per
project/tenant.

### Schema drift
Agents on different versions must not corrupt the log. Events carry
`schema_version`; migrations are forward-only and never rewrite history.
