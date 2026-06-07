# Pre-registration: exploration reward (ADR-0015)

Status: sealed at run-start under `praxis_git_sha`.

The reward function is observability-only. The agent does not see it.
The formula, alpha, canonicalization rule, and resolution criterion are
locked here BEFORE any Phase 2 experiment computes a reward number.
Changing any of these after a run starts invalidates that run's data
(ADR-0009 precedent on prompt changes applies identically; ADR-0015 sec 7).

## Formula

```
reward = (resolved_uncertainties + alpha * new_unique_candidate_risks) / budget_tokens
```

Where:

- `resolved_uncertainties` is the count of `Uncertainty` entries that
  flipped from `resolved=False` to `resolved=True` during the run,
  attributable to E-mode observations via the event log.
- `new_unique_candidate_risks` is the count of new `CandidateEvent`
  entries of kind `risk` written by this run that are not duplicates of
  an existing risk under the canonicalization rule in section 3 of
  ADR-0015.
- `alpha` is a sealed constant; the initial registered value is `0.5`.
- `budget_tokens` is the token budget the run consumed (same denominator
  Phase 1 used as cost axis).

## Sealed parameters

```json
{
  "praxis_git_sha": "<filled at run-start by experiments/multi_writer/run.py>",
  "alpha": 0.5,
  "canonicalization_rule_id": "adr-0015-sec-3-trigger-validator-v1",
  "resolution_criterion": "Uncertainty.resolved transitions False->True during the run, with the resolving_signal_value populated by an ObservationEvent emitted by the E-mode runner (agent_id == agent_identity per ADR-0008).",
  "formula": "reward = (resolved_uncertainties + alpha * new_unique_candidate_risks) / budget_tokens"
}
```

The sealed object hashes to a `seal_id` (16-hex SHA-256 prefix). Any two
runs whose `seal_id` differs cannot be aggregated; the seal id
discrepancy is the LOUD signal that the formula or its parameters
drifted between runs. See
`src/praxis/metrics/exploration_reward.py::RewardSeal`.

## Canonicalization rule

Two candidate risks count as the SAME risk iff their canonical trigger
key matches. The key is computed by
`praxis.metrics.exploration_reward.canonical_trigger_key`:

- HTTP triggers: `http|<METHOD>|<lowercased-path>|<json-sorted body/params>`.
- Sequence triggers: `sequence|<n>|<lowercased-whitespace-collapsed action>`.

The `expect` free-text predicate is intentionally NOT part of the key
(ADR-0015 sec 3): a candidate that fails the ADR-0009 trigger validator
cannot enter the count at all, so phrasing differences in `expect`
cannot inflate the score.

## Resolution criterion

`Uncertainty.resolved` flips False->True via a projection over events
written by E-mode (`agent_id == agent_identity`). The projection layer
attributes the flip; this module consumes a count and does not
re-implement projection. Source-independence (ADR-0008) is preserved
because attribution keys on agent identity, not run UUID.

## New observability metrics (paired with the reward)

ADR-0015 sec 6 ships two sibling metrics, sealed under the same
`praxis_git_sha`:

- `unique_candidates_per_budget` floor: minimum unique candidate risks
  per 1000 tokens of E-mode budget. Numeric value calibrated against
  the first dry run; this pre-registration commits the existence of
  the floor. Value placeholder pending the first dry run:
  `unique_candidates_per_1000_tokens >= 0.5` (i.e., on a 10k-token
  E-mode budget, at least 5 new unique candidate risks land in the
  store; runs below the floor are LOUD-flagged on the report).
- `goodhart_score` red-flag heuristic: ratio of
  `new_unique_candidate_risks` that subsequently fail to promote to
  `believed` within N follow-up runs to total new candidates written.
  N pre-registered as 3 follow-up multi-writer runs; this metric
  requires the run-after-the-first to compute.

Both are observability metrics. Neither feeds the agent.

## Random-walk baseline (ADR-0015 sec 5)

On the first Phase 2 multi-writer experiment, a `random_walk` arm runs
concurrently with the `memory` arm. Same budget, same SUT, same
release, same release-level `praxis_git_sha`. `random_walk` receives no
risks and no uncertainties as input; it walks the surface at random
given only the goal string. Both arms compute reward via the formula
above.

If `memory`'s reward does not significantly exceed `random_walk`'s
reward, the exploration incentive has failed and Phase 2 returns to
the kill/continue gate (`docs/04`).

## Forbidden alternatives (mirrors ADR-0015 sec 7)

DO NOT:

- Feed `reward` or any component back into the agent prompt, prompt
  selector, budget allocator, or any subsequent E-mode input.
- Change alpha, the canonicalization rule, or the resolution criterion
  after a run starts. Such changes invalidate prior data and the
  RewardSeal id divergence is the LOUD signal.
- Report the reward in any official run record without the paired
  `goodhart_attacks.md` deliverable.
- Substitute a looser uniqueness rule (lexical diff, embedding
  distance, judge-similarity) for the canonical-trigger-key rule.
- Run the memory arm without the random-walk baseline arm. A reward
  number reported solo is uninterpretable by construction.
