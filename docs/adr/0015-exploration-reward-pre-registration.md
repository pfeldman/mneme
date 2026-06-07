# ADR-0015: Exploration reward function: pre-registered, observability-only for Phase 2

Status: Proposed

## Context

Phase 2 introduces an explicit exploration incentive so concurrent
writers do not silently converge on the happy path and shrink
coverage (AGENTS.md Phase 2 brief; docs/05; docs/06). The Phase 2
plan asks two related questions that share one failure mode:
(P2-06) how is E-mode's value measured at all; (P2-07) what stops
that measurement from being gamed once the agent can see the score.
Both are the same Goodhart trap: a reward function visible to the
optimizer becomes the target, and the target degenerates the moment
the proxy diverges from the property the project cares about (unique
operational knowledge about a specific system under test).

ADR-0009 pinned one floor against this for Phase 1: `off_path_fraction`
(>= 0.4) kills if E-mode collapses into R-mode. ADR-0010 records that
gate passing at 0.69. But `off_path_fraction` is a degeneracy
detector, not a reward function. Phase 2 needs a reward to know
whether E-mode is worth its tokens, and it must be pre-registered
before any optimization (sealed alpha, sealed uniqueness rule, sealed
adversarial review) for the same reason the Phase 1 falsifier was:
a reward tuned after seeing the data is a self-fulfilling prophecy
(ADR-0009 precedent).

ADR-0014 makes "new unique candidate risks" countable across sessions
for the first time; this ADR can therefore commit to counting
persisted candidates without re-litigating that boundary.

## Decision

### 1. The reward formula is pre-registered, exactly this shape

```
reward = (resolved_uncertainties + alpha * new_unique_candidate_risks) / budget_tokens
```

Where:

- `resolved_uncertainties` = count of `uncertainties` entries that
  flipped from open to resolved during the run, attributable to
  E-mode observations via the event log.
- `new_unique_candidate_risks` = count of `CandidateEvent` entries of
  kind `risk` written by this run that are not duplicates of an
  existing risk (any status) under the canonicalization rule in
  section 3.
- `alpha` = pre-registered constant, sealed under `praxis_git_sha` at
  run-start; initial value 0.5, recorded here so it cannot drift
  silently between runs.
- `budget_tokens` = token budget the run consumed (same denominator
  Phase 1 used as the cost axis).

The two countable outputs are the durable E-mode signal: an
uncertainty resolved is one less thing the next agent must probe; a
new unique candidate risk is a new probe location persisted to the
store. Both are knowledge, not procedure.

### 2. Observability-only for Phase 2; the agent does not see the reward

Reward is computed at projection time by the report layer. It is NOT
fed back into E-mode prompts. It does NOT modify agent state, prompt
selection, run scheduling, budget allocation, or any other input to
a subsequent E-mode invocation. Phase 2 reads it; Phase 2 does not
optimize against it. The instant the reward is visible to the
optimizer, the canonicalization rule below stops being defense and
becomes attack surface.

### 3. `unique` is canonicalized by the trigger validator from ADR-0009

Two candidate risks count as the SAME risk if their `trigger` fields
canonicalize to the same structured form under the ADR-0009 trigger
validator. Canonicalization compares the parsed structured form, not
the surface string: `POST /coupon/apply with {coupon:"SAVE10",
subtotal:49}` and `POST /coupon/apply with subtotal=49 and
coupon=SAVE10` canonicalize identically. A risk that fails the
trigger validator does not enter the store (ADR-0009 already enforces
this); it cannot be counted toward `new_unique_candidate_risks`. No
new lexer, no new normalization rule, no opportunity for the reward
to incentivize syntactic gaming the validator does not already reject.

### 4. Adversarial Goodhart review is a pre-run gate, not a follow-up

Before the first Phase 2 experiment that reports this reward, a
separately-isolated session (not the session that wrote this ADR,
not the session that authored the Phase 2 prompts) produces:

- >= 8 distinct named attack vectors on the formula. Minimum
  coverage: trigger-validator bypass, uncertainty-inflation,
  budget-denominator attacks, candidate-fanout, alpha-tuning,
  same-agent-multi-writer attacks (per ADR-0012 + ADR-0008),
  stale-recycling (per ADR-0013), observability-leakage.
- For each: predicted reward inflation (qualitative range fine;
  direction and approximate magnitude is what counts).
- For each: either (a) a mitigation that lands in the same Phase 2
  commit as the reward instrumentation, or (b) an explicit "accepted
  rationale" naming the invariant from another ADR that bounds the
  attack (diversity rule, append-only, source-independence).

No Phase 2 experiment may report this reward until the deliverable
exists, is reviewed, and lands in `experiments/multi_writer/`. The
review session is sealed under its own `praxis_git_sha` so attacks
discovered after the fact land as new entries, never as edits.

### 5. Random-walk baseline runs concurrently with the memory arm

On the first Phase 2 multi-writer experiment, a `random_walk` arm
runs concurrently with the `memory` arm, same budget, same SUT, same
release. `random_walk` receives no risks and no uncertainties as
input; it walks the surface at random given only the goal string.
Its reward is computed by the same formula. If `memory`'s reward
does not significantly exceed `random_walk`'s reward, the
exploration incentive has failed: Phase 2 returns to the
kill/continue gate (docs/04). This is the structural twin of the
Phase 1 `cold_readme` arm: a steelmanned baseline that closes the
cheap-explanation loophole.

### 6. New floors and a red-flag heuristic, paired with the reward

Two new observability metrics ship with the reward and inherit the
sealed-under-praxis_git_sha rule:

- `unique_candidates_per_budget` floor: minimum unique candidate
  risks per 1000 tokens of E-mode budget, below which the run is
  flagged. Numeric value pre-registered in the Phase 2 experiment
  design document (calibrated against a dry run); this ADR commits
  the rule that it exists and is sealed.
- `goodhart_score` red-flag heuristic: ratio of
  `new_unique_candidate_risks` that subsequently fail to promote to
  `believed` within N follow-up runs (N pre-registered) to total
  new candidates written. High `goodhart_score` indicates candidates
  passing the canonicalization filter but never gathering diverse
  corroboration, i.e. the formula being gamed at the write boundary.

Both are observability metrics. Neither feeds the agent. Both fire
LOUD warnings on the run report so the Phase 2 verdict ADR (parallel
to ADR-0010) can cite them by name. The existing `off_path_fraction`
floor from ADR-0009 stays.

### 7. Forbidden alternatives

DO NOT:

- Feed `reward` or any component back into the agent prompt, prompt
  selector, budget allocator, or any input that influences a
  subsequent E-mode run during Phase 2.
- Change `alpha`, the canonicalization rule, or the resolution
  criteria after a run starts. Such changes invalidate prior data
  (ADR-0009 precedent on prompt changes applies identically).
- Report the reward in any official run record without the paired
  Goodhart adversarial-review deliverable from section 4.
- Substitute a looser uniqueness rule (lexical diff, embedding
  distance, judge-similarity) for the trigger-validator
  canonicalization in section 3.
- Run the memory arm without the random-walk baseline arm in the
  same experiment. A reward number reported solo is uninterpretable
  by construction.

## Consequences

+ Phase 2 has a numeric, pre-registered reward function for E-mode
  decoupled from the agent's optimization loop. Exploration incentive
  becomes measurable without becoming a target. Invariant respected:
  `exploration-incentive-against-coverage-collapse`.
+ The formula re-uses ADR-0009's trigger validator for uniqueness,
  inheriting the schema-rot defense already in place: a candidate
  that cannot pass the validator cannot inflate the score. Invariant
  respected: `loud-and-traceable-over-silent-and-convenient`.
+ The Goodhart adversarial review is a hard gate, not a follow-up
  bullet. It lands in `experiments/multi_writer/` before the first
  run.
+ The random-walk baseline forces the moat to demonstrate it survives
  the exploration axis the same way ADR-0009 forced it on the
  regression-recall axis. Invariant respected:
  `no-silent-success-when-app-broken` (extended to "no silent success
  when the metric is broken").
+ The reward derives from event-log entries that already carry
  provenance per ADR-0004. Invariant respected:
  `provenance-and-confidence-mandatory`.

- Reporting a metric IS optimizing for it. Even though section 2
  forbids feeding the reward back into the agent, the engineers
  iterating on E-mode prompts WILL see the numbers and WILL adjust
  prompts toward them. This is unavoidable; the Goodhart review
  exists precisely to surface attacks BEFORE that drift bakes in,
  and alpha is sealed precisely so the drift cannot be hidden by a
  post-hoc rescale.
- The formula commits to two countable components; a real
  exploration moat may have value not captured by either (e.g. agent
  surfaces a subtle invariant we did not know to ask about). Those
  wins land as new seeded oracles via human review, outside the
  reward loop, and are not double-counted.
- `goodhart_score` requires N follow-up runs to compute, so a single
  Phase 2 experiment cannot report it immediately. The first run
  reports the immediate reward plus the
  `unique_candidates_per_budget` floor; `goodhart_score` lands in
  the run after.
- The random-walk baseline costs an additional concurrent arm of
  budget. Accepted: the cost is dominated by the multi-writer
  harness itself and the steelmanned-baseline pattern has paid for
  itself once already (Phase 1 `cold_readme`).

### Invariants respected

- `exploration-incentive-against-coverage-collapse`
- `loud-and-traceable-over-silent-and-convenient`
- `no-silent-success-when-app-broken`
- `provenance-and-confidence-mandatory`

### Invariants explicitly NOT covered by this ADR

- `tenant-scoping-prevents-leakage` (covered by ADR-0012; the reward
  reads from a single-tenant store by contract).
- `concurrent-writes-lose-no-knowledge` (covered by ADR-0012; the
  reward consumes the projection).
- `no-secrets-tokens-pii-in-knowledge`: not addressed here because
  the reward components are integer counts (not payloads), so the
  formula has no surface on which secrets, tokens, or PII could
  appear. The substantive defense is intrinsic to the formula.

## Relation to prior ADRs

Extends ADR-0009: re-uses the `risks.trigger` structured validator as
the uniqueness canonicalization rule, and re-uses the pre-registration
discipline (alpha + resolution criteria sealed under `praxis_git_sha`
at run-start; changes invalidate prior data). Adds two sibling floors
(`unique_candidates_per_budget`, `goodhart_score`) without modifying
the existing `off_path_fraction` floor. Depends on ADR-0014: the
`new_unique_candidate_risks` count requires `CandidateEvent` to
persist candidates across sessions. Depends on ADR-0012 and ADR-0008:
`source_id = agent_identity` under multi-writer ensures concurrent
E-mode runs of the same model count as one source for promotion,
which prevents same-agent fanout from inflating the future
`believed`-promotion count that feeds `goodhart_score`. Does not
modify ADR-0010's Phase 1 verdict.
