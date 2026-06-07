# ADR-0008: Type-diversity needs source-independence

Status: Accepted — IMPLEMENTED (the fix landed; see "Update — implemented" below)

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
Harden promotion so that `believed` requires BOTH evidence-type diversity AND
source-independence: the stable success signals must span ≥2 distinct `type`s AND
≥2 distinct `source_id`s (a seed counts as one independent source). A single source
can no longer self-corroborate across types.

## Update — implemented (2026-06-07)
Implemented in `oracle/trust.py` as `independent_diverse(...)`; `oracle_believed`
and `classify` now use it (`merge` passes `oracle_independent`). Re-running the
stress harness:
- `single_source_two_types`: **0** (was a breach) — closed. One source asserting
  two types is held `contested`, never promoted.
- correlated agents (N≤100), lone type, contradiction, oscillation, stale: still 0.
- positive control (2 sources × 2 types): still believed (no over-paranoia).

On implementation we found `seed_rides_single_agent` (a correct seed of one type +
a single agent of another type) is **structurally identical to the legitimate
cold-start corroboration pattern** (the login example): seed counts as one source,
the agent as a second, so it is `believed`. The oracle cannot distinguish an honest
single observation from a fabricated one, so this is the **inherent trust boundary**,
not a fixable gap. It is mitigated TEMPORALLY — a fabricated signal that does not
reproduce is quarantined (oscillation) or contested (contradiction) — not at
promotion time. Recorded and tested as `INHERENT` in the stress harness.

## Consequences
+ Single-source self-corroboration across types can no longer poison the oracle;
  resistance is regression-tested (`tests/test_oracle_stress.py`).
+ The one residual case (seed + single agent) is named as an inherent boundary and
  mitigated over time, not hidden behind a happy-path false_pass of 0.
- Requiring ≥2 sources reintroduces a source dimension ADR-0005 de-emphasized;
  ADR-0005 is refined, not reversed (same-type repeats still grant no independence,
  and a seed alone still makes the oracle believed from cold start).
- The inherent boundary means a single fabricated different-type observation, on top
  of a trusted seed, is believed until contradicted. Multi-writer trust still relies
  on temporal mitigation (quarantine/contest) and the Phase-3 governance layer.
