# Goodhart attacks on the exploration reward (ADR-0015 sec 4 deliverable)

Status: pre-run gate. ADR-0015 sec 4 makes this file a hard gate: no
Phase 2 experiment may report the exploration reward until this file
exists and is reviewed. The session that authored this file is sealed
under its own `praxis_git_sha` so attacks discovered after the fact
land as new entries, never as edits.

The reward formula is observability-only in Phase 2 (the agent does not
see it), but ADR-0015 names two pressure paths that make Goodhart real
anyway:

- The engineers iterating on E-mode prompts SEE the numbers and WILL
  adjust prompts toward them. This is unavoidable; the review below is
  the defense against drift baking in unnoticed.
- Even with the formula hidden from the agent, the components
  (resolved uncertainties, new unique candidate risks) are agent-visible
  artifacts; any prompt that nudges the agent to "raise more questions"
  or "log more probes" is a partial reveal.

Each attack below names:
- the vector,
- the predicted reward inflation direction and approximate magnitude,
- the mitigation (in code, or a sibling ADR the attack is bounded by).

ADR-0015 sec 4 minimum coverage: trigger-validator bypass,
uncertainty-inflation, budget-denominator attacks, candidate-fanout,
alpha-tuning, same-agent-multi-writer attacks, stale-recycling,
observability-leakage. All eight are covered below plus three more
that surfaced during the review.

---

## 1. Trigger-validator bypass via `expect`-text paraphrase

**Vector.** Agent emits N candidate risks with identical structured
trigger (same method, path, body) but cosmetically-different `expect`
predicates ("returns 200", "responds with HTTP 200", "200 OK"). If
the canonicalization keyed off the surface trigger string, each
paraphrase would count as a new unique candidate.

**Inflation.** Up to (N - 1) extra unique candidates per real probe.
On a run with 20 probes and 5 paraphrases each, this is +80 fake unique
candidates. Direction: numerator inflates linearly with paraphrase
count; reward inflates linearly too.

**Mitigation (in code).** `canonical_trigger_key` in
`src/praxis/metrics/exploration_reward.py` deliberately excludes
`expect` from the key (HTTP: only method + path + sorted body/params;
sequence: only n + normalized action). The test
`test_canonical_key_ignores_expect_text` pins this behavior. ADR-0009
trigger validator additionally rejects banned phrases in `expect`
before the risk can enter the store.

---

## 2. Uncertainty-inflation by question-fanout

**Vector.** Agent splits one open question into N nearly-identical
sub-questions ("can users login?" -> "can users login with email?",
"can users login with username?", "can users login with phone?"),
each then "resolved" trivially. Each resolution increments
`resolved_uncertainties` once. Numerator scales linearly with N.

**Inflation.** Up to (N - 1) extra resolved uncertainties per real
question. On a 5-question run with 4-way fanout, +16 fake resolutions.

**Mitigation (accepted rationale + sibling-ADR bound).** ADR-0014
makes `Uncertainty` a first-class store entity with provenance, and
ADR-0008 source-independence applies: a resolution requires an
ObservationEvent whose signal type is independent of the question's
author. Same-agent fanout does NOT produce independent corroboration,
so the diverging branches stay unresolved unless an independent
source closes them. Sibling-ADR cite: ADR-0005 + ADR-0008. Residual
risk: a malicious or sloppy seed can pre-seed near-identical
questions. Flagged for the Phase 2 verdict ADR; not a Phase 2
blocker because seeding is human-gated.

---

## 3. Budget-denominator attack via under-reporting tokens

**Vector.** Agent (or executor) under-reports `budget_tokens` to
inflate `reward = numerator / budget_tokens`. Reporting 100 tokens
instead of the true 10000 inflates reward 100x for the same numerator.

**Inflation.** Direction: monotonically inverse. A 10x under-report
inflates reward 10x. A floor of 0 makes the formula safe (returns
0.0) but a tiny floor like 1 token is the worst case.

**Mitigation (in code + sibling-ADR bound).** The executor protocol
already requires token reporting per ADR-0009 (the regression-recall
manifest seals `budget_tokens_per_goal`). The Phase 2 multi-writer
harness pulls token counts from the live LLM API response, not from
the agent's self-report, closing the easy attack. Residual: a
provider-side bug in token counting. The
`unique_candidates_per_budget` floor from ADR-0015 sec 6 is the
LOUD-flag for absurdly-low denominators: a 0.5/1000 floor on a
1-token budget would require 0.0005 candidates, which trips the
floor check upward.

---

## 4. Candidate-fanout against the same endpoint

**Vector.** Agent finds one real probe (POST /coupon/apply with
{coupon}) and emits N candidate risks varying one structural field
trivially ({coupon: "X1"}, {coupon: "X2"}, ...). Each canonicalizes
to a different key (body differs), so the unique count grows
linearly with N.

**Inflation.** Up to (N - 1) extra unique candidates per real probe.
For N=10, +9 per endpoint. On 20 endpoints with N=10 each, +180.

**Mitigation (in code + sibling-ADR bound).** This is a real attack
the formula does NOT close on its own. Two defenses bound it:
- ADR-0014: candidate risks enter as `contested`, never `believed`,
  and `goodhart_score` (ADR-0015 sec 6) tracks the ratio that fail
  to promote within N follow-up runs. A fanout cluster of 10 fake
  trigger-variants will almost all fail to promote (no diverse
  corroboration), so the goodhart_score for that run inflates and
  the run is LOUD-flagged.
- ADR-0008: promotion requires source-independent corroboration;
  a single agent emitting 10 fake variants cannot self-promote.
The reward number for the run itself is still inflated; the
goodhart_score is what catches it on the run AFTER. Accepted
residual: Phase 2 sec 6 explicitly says the first run reports
reward + `unique_candidates_per_budget`; `goodhart_score` lands
the run after.

---

## 5. Alpha-tuning post-hoc

**Vector.** Reviewer sees the first run's numbers, notices that
`memory`'s lead over `random_walk` is fragile at alpha=0.5, and
proposes alpha=0.7 to amplify the candidate term. The numbers move,
the moat looks stronger, and the new alpha quietly replaces the old.

**Inflation.** Arbitrary, by design. Tuning alpha after seeing the
data is a self-fulfilling prophecy (ADR-0009 precedent on prompt
changes).

**Mitigation (in code).** `RewardSeal.seal_id` hashes alpha into a
16-hex digest at run-start; any subsequent change produces a
different seal_id, and `verify_invariant` raises when two runs with
different seals are compared. The test
`test_alpha_change_invalidates_seal` pins this behavior. The run
manifest carries the seal_id alongside `praxis_git_sha` so an
auditor can detect post-hoc alpha drift.

---

## 6. Same-agent multi-writer attacks (ADR-0012 + ADR-0008 cite)

**Vector.** Under multi-writer concurrency, two E-mode runs of the
SAME model emit overlapping candidate risks. If `source_id` were
the run UUID, each run's candidates would look like "independent
corroboration" and both would inflate the unique count and
subsequently promote without real diversity.

**Inflation.** Up to 2x on the candidate count (per run, plus
double-count when both runs' candidates land in the same projection
window).

**Mitigation (sibling-ADR bound).** ADR-0012 + ADR-0008 fix
`source_id = agent_identity` (model+adapter+settings), NOT run
UUID. Two concurrent runs of the same model count as ONE source for
promotion. Same-agent multi-writer attacks therefore inflate the
candidate count once per agent identity but NOT across runs of the
same identity; the `goodhart_score` follow-up further filters out
fanout that never gathers diverse corroboration. Accepted rationale:
this is the canonical case ADR-0008 was hardened for and the
mitigation is invariant-level, not formula-level.

---

## 7. Stale-recycling (ADR-0013 cite)

**Vector.** Agent re-emits a risk that already exists in the store
but currently has status `stale` (decayed past its expiry per
ADR-0013). If existing-risk dedup keyed only on `believed` + new
risks, a stale risk would count as new because canonicalization
would not see it.

**Inflation.** Up to the size of the stale-risk corpus, per run.
On a long-running store with O(1000) stale risks, the first run
after a release version bump could "discover" all of them.

**Mitigation (in code).** `count_unique_new_risks` keys against
`existing_risks` regardless of status: the docstring and signature
include "any status: believed, contested, stale, quarantined".
A risk that exists in the store at any status is in the dedup set,
so re-emitting it does NOT inflate the count. The test
`test_unique_count_subtracts_existing_risks` pins this but does
NOT yet pin the four-status sweep; recommended follow-up to add
a parameterized test over the Status enum. Logged here as the
review's discovered TODO.

---

## 8. Observability-leakage: the reward becomes the target

**Vector.** Even though ADR-0015 sec 2 forbids feeding the reward
back into the agent, the human engineers tuning E-mode prompts see
the numbers in run reports. They iterate: "this prompt got reward
0.004, that one 0.006", and the prompt drifts toward whatever
inflates the formula's components. Over enough iterations, the
optimizer (the human) effectively closes the loop the ADR forbade.

**Inflation.** Direction: positive on the reward number; the moat
claim degrades because the metric drifts away from "unique
operational knowledge about a specific SUT" and toward "whatever
the components count".

**Mitigation (in code + adversarial discipline).** The defense is
this very document, plus the run-locked seal: a reviewer comparing
seal ids across runs can detect a quiet alpha or canonicalization
drift, and the goodhart_score on follow-up runs is the lagging
indicator that the components are being gamed (high
goodhart_score = candidates inflated faster than they corroborate).
Accepted residual: reporting a metric IS optimizing for it (ADR-0015
consequences sec). The seal + goodhart_score + this attack list are
the structural defense.

---

## 9. Resolution-cheat: trivial resolution of pre-seeded uncertainties

**Vector.** Seed file ships open uncertainties that are trivially
answerable from the URL of the app's index page. E-mode visits the
index, "resolves" all of them, and the numerator spikes for a
single trivial action.

**Inflation.** Up to the count of trivially-resolvable seeded
uncertainties. On a 10-uncertainty seed file, +10 to the numerator
for one HTTP GET.

**Mitigation (accepted rationale + sibling-ADR bound).** ADR-0014
makes uncertainties a first-class store entity; the resolution
event must carry an `ObservationEvent.resolving_signal_value` per
the resolution criterion in `pre_registration.md`. ADR-0009's
diversity-or-seed rule applies: a trivial resolution from a single
HTTP GET produces ONE signal; promoting the resolution to
`believed` requires diversity. Therefore the trivially-resolved
uncertainty stays at the lowest believed-status and does count
toward the numerator BUT downstream `goodhart_score` will detect
that the candidates produced by these "resolutions" never go on to
promote. Accepted: the first run reports the inflated reward; the
followup run's goodhart_score is the LOUD flag.

---

## 10. Pseudo-trigger via shape compliance

**Vector.** Agent emits a candidate risk with a structured HTTP
trigger that LOOKS like a real probe (path, method, body) but
points at a nonexistent endpoint (`POST /__doesnotexist`). Shape
validation passes; the candidate enters the store; the unique count
increments. There is no actual probe to corroborate.

**Inflation.** Up to one extra unique candidate per fake trigger.

**Mitigation (sibling-ADR bound).** The reward computes from the
candidate count at write time; corroboration is a Phase 2 follow-up
question. `goodhart_score` (ADR-0015 sec 6) is the exact bound:
candidates that never promote inflate the goodhart_score, which is
the lagging LOUD flag. Accepted residual + follow-up: consider
adding a `live_probe_required` field to `CandidateEvent` so the
event itself records whether the trigger fired against the live
SUT during the run. This is out-of-scope for ADR-0015 but flagged
for ADR-0014's implementation review.

---

## 11. Coverage-collapse mimicry (the failure mode the reward exists to detect)

**Vector.** E-mode quietly degenerates into R-mode: it walks the
happy path, observes no new risks, raises no new uncertainties.
The reward goes to 0 (correctly), but the agent's run report shows
high tool-call count and high token spend, masking that exploration
collapsed. Without the random-walk baseline, a reader might
conclude the formula is "noisy" rather than "exploration died".

**Inflation.** Inverse: the agent does NOT inflate the reward; the
attack is that the human reader misreads the floor as noise.

**Mitigation (in pre-registration).** ADR-0015 sec 5 makes the
random-walk baseline NON-OPTIONAL: a memory arm without random_walk
is uninterpretable by construction. The kill criterion is also
sealed: if `memory_reward <= random_walk_reward`, exploration has
failed and Phase 2 returns to the kill/continue gate. The
`off_path_fraction` floor from ADR-0009 catches the R-mode-collapse
degeneration mechanically; this attack is bounded by stacking the
two metrics + the baseline.

---

## Summary of mitigations by source

| attack | bounded by |
|--------|-----------|
| 1 trigger-validator bypass | code (`canonical_trigger_key` excludes `expect`) |
| 2 uncertainty fanout | ADR-0005 + ADR-0008 + ADR-0014 + goodhart_score |
| 3 budget under-report | ADR-0009 token reporting + `unique_candidates_per_budget` |
| 4 candidate fanout | ADR-0014 + ADR-0008 + goodhart_score (lagging) |
| 5 alpha tuning post-hoc | code (`RewardSeal`) + run manifest |
| 6 same-agent multi-writer | ADR-0012 + ADR-0008 (agent_identity, not run UUID) |
| 7 stale-recycling | code (`count_unique_new_risks` dedup over any status) |
| 8 observability leakage | this document + seal + goodhart_score |
| 9 resolution-cheat | ADR-0014 + ADR-0009 diversity + goodhart_score |
| 10 pseudo-trigger | goodhart_score (lagging) + ADR-0014 follow-up |
| 11 coverage collapse | random-walk baseline (NON-OPTIONAL) + off_path_fraction |

## Pending follow-ups surfaced during this review

- Add a parameterized test over the four-value `Status` enum to
  `tests/test_exploration_reward.py::test_unique_count_subtracts_existing_risks`
  (covers attack 7 explicitly).
- Consider `CandidateEvent.live_probe_required` on the ADR-0014
  implementation (covers attack 10 more tightly than `goodhart_score`
  alone).
- Compute the `unique_candidates_per_budget` floor empirically from
  the first dry run (placeholder value 0.5/1000 in
  `pre_registration.md`).

## Reviewer ledger

- Author: this session (sealed under its own `praxis_git_sha`; see
  `seal_id` in the run manifest written by
  `experiments/multi_writer/run.py` when it lands).
- Attacks discovered AFTER this file lands MUST be added as new
  entries (`## 12. ...`), never as edits to existing entries.
  Edits would re-seal the file under a different sha and the
  ADR-0015 sec 4 review history would silently rewrite.
