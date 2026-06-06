# ADR-0004: Provenance + confidence are mandatory on every assertion

Status: Accepted

## Context
The existential risk (docs/06) is silent poisoning: a confidently-wrong success
signal makes shared memory worse than no memory. You cannot resolve conflicts,
decay stale facts, or run the oracle without knowing who said it, when, how
often, and how independently.

## Decision
Every assertion-like node (recognition signal, success/failure signal, risk,
path) MUST carry `provenance` (source_agent, last_verified, observation_count,
independent_sources) and `confidence`, plus a `status`
(believed/contested/stale/quarantined). The schema enforces this; validation
rejects entries that omit it.

## Consequences
+ Conflict resolution, decay, and oracle trust scoring become possible.
+ A success oracle can require >=2 independent sources before it is "believed".
- Slightly heavier to write; the model/adapters should fill provenance automatically.
