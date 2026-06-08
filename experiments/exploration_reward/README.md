# Exploration reward (observability-only, ADR-0015)

Phase 2 introduces an explicit exploration incentive so concurrent
writers do not silently converge on the happy path and shrink coverage
(`docs/05`, `docs/06`, AGENTS.md Phase 2 brief). This directory hosts
the pre-registered artifacts the ADR demands BEFORE any Phase 2
experiment may report the reward number.

The reward formula is locked verbatim in ADR-0015 sec 1:

```
reward = (resolved_uncertainties + alpha * new_unique_candidate_risks) / budget_tokens
```

The implementation lives in `src/praxis/metrics/exploration_reward.py`.
This directory holds:

- `pre_registration.md` - alpha + resolution criterion + canonicalization
  rule, sealed under `praxis_git_sha` at run-start.
- `goodhart_attacks.md` - the >= 8 named attack vectors + mitigations.
  ADR-0015 sec 4 makes this a hard pre-run gate: no Phase 2 experiment
  may report this reward until this file exists and lands in the same
  commit as the reward instrumentation.
- `metrics.py` - thin wrapper that consumes a run's projection and
  produces a `RunReward` row. Composes
  `src/praxis/metrics/exploration_reward.py`; does not re-implement the
  formula.

Observability-only contract (ADR-0015 sec 2): the reward does NOT feed
back into agent state, prompt selection, or budget allocation in Phase 2.
The instant the reward is visible to the optimizer, the canonicalization
rule stops being defense and becomes attack surface. The Goodhart
adversarial review (`goodhart_attacks.md`) exists precisely to surface
attacks before the engineers iterating on E-mode prompts see the
numbers and adjust toward them.

Random-walk baseline (ADR-0015 sec 5): on the first Phase 2 multi-writer
experiment, a `random_walk` arm runs concurrently with the `memory` arm
under the same budget on the same SUT. Both arms compute reward via the
same formula. `random_walk` receives no risks and no uncertainties as
input. If `memory` does not exceed `random_walk`, the exploration
incentive has failed and Phase 2 returns to the kill/continue gate.
