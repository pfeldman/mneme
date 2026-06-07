# ADR-0008: Type-diversity needs source-independence (Phase-1 oracle hardening)

Status: Accepted (records a gap found by adversarial stress; fix deferred to Phase 1)

## Context
ADR-0005 promotes a success oracle to `believed` when ≥2 success signals of
DIFFERENT `type` agree, treating evidence type as the independence axis. The
adversarial stress harness (`experiments/ui-mutation/oracle_stress.py`) confirms
this defeats the attacks ADR-0005 targeted:
- a lone single-type signal is never believed;
- N agents asserting the same single-type signal (N up to 100) never become
  believed — agent count is not independence;
- contradiction → `contested`, oscillation → `quarantined`, off-version/aged → `stale`;
- and genuine 2-source/2-type evidence IS still believed (no over-paranoia).

But two PROBE scenarios breach the SPIRIT of the rule:
1. **single_source_two_types** — ONE agent fabricates a behavioral AND a network
   signal; both are promoted, because the rule counts type-diversity regardless of
   source. A single hallucinating source produced both → not independent.
2. **seed_rides_single_agent** — a correct human/spec seed supplies the diversity,
   so a single agent's different-type fabrication rides to `believed`.

Type-diversity WITHOUT source-independence is a poisoning vector: the failure mode
docs/06 calls the product-killer.

## Decision
Record the gap now; do not silently change the trust rule. In Phase 1, harden
promotion so that `believed` requires BOTH evidence-type diversity AND
source-independence — e.g. the ≥2 agreeing types must come from ≥2 distinct
`source_id`s (a seed counts as one independent source; a second, different-type
signal must come from a different source than the first to corroborate). Until then,
the gap is explicit, tested (`tests/test_oracle_stress.py`), and loud — never silent.

## Consequences
+ The remaining oracle risk is named, measured, and regression-tested rather than
  hidden behind a clean happy-path false_pass of 0.
+ Phase-1 has a concrete, testable hardening target.
- The current oracle can be poisoned by a single confidently-wrong source that
  fabricates two evidence types. Do not trust shared, multi-writer memory for a
  goal whose oracle rests on single-source diversity until this lands.
- Requiring ≥2 sources reintroduces a source dimension ADR-0005 de-emphasized;
  ADR-0005 is refined, not reversed (same-type repeats still grant no independence).
