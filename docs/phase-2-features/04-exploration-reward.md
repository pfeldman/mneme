# Exploration reward (ADR-0015)

Every Praxis exploration run gets a single number that scores how much useful new knowledge it produced for each token it spent. The number combines two things the run leaves behind: open questions it closed, and brand-new probe locations it added. The agent never sees this number, so it cannot adjust its behavior to inflate it. Engineers read the number after the fact to decide whether exploration is paying for itself.

## Why this exists

Phase 2 lets multiple agents explore the same target system at the same time. Without an incentive to spread out, they collapse onto the "happy path" and stop finding new things. We needed a reward that says "this run was worth its tokens" or "it was not", without creating a target the agent can game. The classic trap: the moment an optimizer can see its own score, the score stops measuring what we care about and starts measuring whatever shortcut the optimizer found. Pre-registering the formula (writing it down before any optimization) and hiding it from the agent are how we avoid that trap.

## How to use it

You do not invoke this from the agent. The reward runs as part of the experiment report at the end of a multi-writer run. Two things happen:

1. At run start, the experiment seals the formula parameters (alpha, the uniqueness rule, the resolution criterion) under the current Praxis git SHA. Sealing means: writing them into a record that cannot be changed without invalidating the run's data.
2. At report time, the harness counts the run's outputs and writes a markdown table with one row per arm.

Typical use, from inside an experiment script:

```python
from exploration_reward import metrics as exp_metrics

memory = exp_metrics.ArmRunProjection(
    arm="memory",
    seed=0,
    budget_tokens=1000,
    resolved_uncertainties_new=[...],   # open questions closed in this run
    new_candidate_risks=[...],          # new probe locations written
    existing_risks=[...],               # what was already in the store
)
random_walk = exp_metrics.ArmRunProjection(arm="random_walk", ...)

seal, rows = exp_metrics.compute_experiment_rewards(
    [memory, random_walk], praxis_git_sha="abc123",
)
exp_metrics.write_reward_report(rows, seal, path="report.md")
```

The report shows: per-arm reward, the inputs that produced it, a sibling metric (unique candidates per 1000 tokens), and the seal id so a later reader can verify nothing drifted.

## A worked example

A `memory` arm explores the test coupon endpoint with a 1000-token budget. It closes one open question ("does the coupon endpoint reject expired codes?") and writes two new probe locations the store had not seen before (one for `POST /coupon/apply` with a negative subtotal, one for a sequence trigger "submit checkout twice"). A `random_walk` arm runs at the same time with the same budget but no memory; it writes one new probe (a duplicate of an existing one) and closes nothing.

Reward, applying `reward = (resolved + 0.5 * new_unique) / budget_tokens`:

- `memory`: `(1 + 0.5 * 2) / 1000 = 0.002000`
- `random_walk`: `(0 + 0.5 * 0) / 1000 = 0.000000` (the duplicate does not count)

Memory beats the baseline. The report flags this as expected; if memory had not exceeded random walk, Phase 2 would return to its kill/continue gate.

## What it does NOT do

- It does not feed back into the agent. Nothing in the formula touches prompts, budget allocation, or run scheduling for the next exploration run.
- It does not compare runs with different seals. If alpha or the uniqueness rule changed between two runs, aggregating them raises an error rather than producing a silently meaningless number.
- It does not measure long-term value. A new probe that never gets corroborated by other agents will eventually show up in the `goodhart_score` red flag (computed N runs later), not in this number.
- It does not count "creative" discoveries that the schema cannot represent. Insights outside the trigger / uncertainty model land via human review, not via the reward.

## How to verify it works for you

Run the formula and seal tests:

```bash
pytest tests/test_exploration_reward.py -v
pytest tests/test_exploration_reward_experiment.py -v
```

You should see passes for: deterministic math for fixed inputs, paraphrased triggers collapsing to one count, the `memory` arm refusing to render a report without the `random_walk` arm, and the seal id changing when alpha changes.

## Reference

- ADR-0015 (formal contract): `docs/adr/0015-exploration-reward-pre-registration.md`
- Implementation: `src/praxis/metrics/exploration_reward.py`
- Experiment wrapper: `experiments/exploration_reward/metrics.py`
- Pre-registration artifacts: `experiments/exploration_reward/pre_registration.md`, `experiments/exploration_reward/goodhart_attacks.md`
