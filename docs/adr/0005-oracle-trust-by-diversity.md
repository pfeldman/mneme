# ADR-0005: Oracle trust by evidence diversity; cold-start via seeded oracles

Status: Accepted (refines the promotion rule referenced in ADR-0004)

## Context
ADR-0004 made provenance mandatory, but the first cut promoted a success signal
to `believed` on >=2 *sources* (agents). That is wrong: two instances of the
same model fail identically -- agent count is not independence. And on the first
run nothing is `believed`, exactly when an oracle is most needed (cold-start).

## Decision
1. A goal's success oracle becomes `believed` when EITHER:
   - a success signal has `provenance.source_type` of `human` or `spec`
     (a seeded oracle, trusted from the start), OR
   - >=2 success signals of *different* `type` agree (e.g. behavioral + network).
   Repeated observations of the same evidence type / same endpoint do NOT add
   independence; they only raise confidence within that one signal.
2. Provenance carries `source_type` (human | spec | agent) and `source_id`.
   The naive `independent_sources` counter is removed.
3. Signals that flip-flop across runs are `quarantined`.

## Consequences
+ Cold-start is solved without self-certification.
+ Correlated agent errors no longer masquerade as independent confirmation.
- Trusting a goal now requires either a seed or genuinely diverse evidence; that
  friction is intentional.
