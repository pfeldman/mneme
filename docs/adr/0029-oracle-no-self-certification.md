# ADR-0029: Agent self-observations cannot self-certify the oracle

Status: Accepted (2026-06-08)

## Context

ADR-0005 made the oracle trustworthy by diversity-or-seed: a success signal is
`believed` only when a human/spec seed exists OR >=2 success signals of
DIFFERENT type agree. ADR-0008 hardened that with source-independence: the
diverse types must span >=2 DISTINCT source ids, so one source cannot fabricate
two types and self-corroborate. ADR-0008 also named the INHERENT trust boundary
it cannot close: a correct seed of one type plus a SINGLE agent observation of a
DIFFERENT type is `believed`, because that is structurally identical to
legitimate cold-start corroboration (the login example). The mitigation for the
inherent case is temporal (contradiction -> contested, oscillation ->
quarantined), not at promotion time.

A live `praxis regress` run on the toy testapp surfaced a defect that those
ADRs did not anticipate, because it has two independent causes that compound:

1. Defect A (the runner grows the believed set). `RegressionRunner.run_one`
   persists every agent observation as a first-class `ObservationEvent` on every
   run (`adapter.write_observations(...)`, with `persist_observations=True` by
   default). Unlike `write_candidates`, which routes agent-proposed risks and
   uncertainties to the NON-promotable `CandidateEvent` stream (ADR-0014), an
   `ObservationEvent` is the promotable evidence the projection folds into
   belief. Regress is a READ of the believed oracle (ADR-0009): it confirms the
   seeded signals, it must NOT grow them. But because each run appends the
   agent's confirmations as promotable evidence, the believed success set GREW
   run after run: on the `create-welcome-popup` goal it went from 4 seeded
   believed success signals to 26 agent-sourced ones.

2. Defect B (a lone agent summary borrows the goal-level flag). The per-signal
   classifier promotes an agent success summary to `believed` when the
   GOAL-LEVEL `oracle_independent` flag is True and the summary's type is in the
   agreeing set, EVEN IF that exact summary's only source is a single agent. The
   goal-level flag is computed once over ALL fresh success evidence
   (`merge/projection.py` calls `independent_diverse(fresh_success)`); the seeds
   alone make it True. So an unrelated single-agent paraphrase of a type the
   seeds already cover "borrows" the seed's independence and rides to `believed`,
   even though nothing of a DIFFERENT type from a DIFFERENT source corroborates
   that specific summary.

Together the two defects let regress self-certify: defect A keeps minting
single-agent success summaries into the promotable store, and defect B promotes
each of them on the goal-level flag the seeds set. The believed set inflated to
26 single-agent summaries, which made the goal permanently read as UNCERTAIN
(the regress verdict requires ALL believed success signals to be observed, and
no single run reproduces 26 paraphrases). This violates ADR-0005 (the oracle is
seeded, never self-certified) and ADR-0008 (N runs of one agent are ONE source
and cannot promote).

## Decision

### 1. A success summary is promoted to `believed` only on its OWN merit.

The per-signal classifier (`oracle/trust.py classify`) promotes a success
summary to `believed` only when EITHER:

- the summary is ITSELF seeded (its source types include `human` or `spec`), OR
- the summary ITSELF participates in genuine corroboration: there exists another
  stable success summary of a DIFFERENT type such that the two together span
  >=2 DISTINCT source ids. That is, this summary contributes one of the diverse
  types AND the corroborating evidence comes from at least one source other than
  this summary's own source(s).

A success summary may no longer be promoted by the goal-level
`oracle_independent` flag ALONE. The goal-level flag still gates whether the
goal has an independent-diverse oracle at all (ADR-0008), but it can no longer
hand its independence to an unrelated lone-agent summary that does not
participate in it.

### 2. The legitimate cold-start and the INHERENT boundary are preserved.

The hardened per-signal rule keeps every case ADR-0005 and ADR-0008 intend:

- A seed signal stays `believed` from cold start (the `is_seeded` arm).
- A genuine 2-source / 2-type agent set stays `believed`: each summary has a
  different-type partner from a different source (the positive control).
- The ADR-0008 INHERENT boundary stays `believed`: a correct seed of one type
  plus a SINGLE agent of a DIFFERENT type promotes the agent summary, because
  that agent summary has a different-type partner (the seed) from a different
  source (the seed's source). One genuine corroborating agent observation on a
  seed is still trusted; only same-type self-restatement is now refused.

What the rule now refuses is the defect: a STREAM of distinct single-agent
summaries whose type is one the seeds already cover, with no different-type
partner from a different source, can no longer ride to `believed`.

### 3. R-mode regress does NOT persist agent observations as promotable evidence.

`RegressionRunner.run_one` defaults `persist_observations=False`. Regress
computes its verdict in-memory from the agent's observations
(`verdict_from_observations(kf, ctx.observations)`), so persistence is not
needed to reach the verdict. A regress run reads the believed oracle and reports
OK / REGRESSED / STALE / AUTH-EXPIRED; it never appends promotable
`ObservationEvent`s that would grow the believed success set. The believed set
is grown only by a human/spec seed (teach, ADR-0022) or by genuinely
independent-diverse evidence, never by a confirmation run of one agent.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Let the goal-level `oracle_independent` flag promote a lone-agent success
  summary that does not itself participate in the diverse corroboration. The
  flag gates the goal's oracle, not an unrelated summary's status.
- Weaken any existing RESIST guard to make a test pass: same-type repeats
  (any count), single-source two-types, contradiction, oscillation, and
  off-version/aged evidence must still never reach `believed`.
- Break the INHERENT boundary by requiring a lone agent summary to carry >=2 of
  its OWN sources. A single genuine corroborating agent observation on a seed
  (a different type from a different source) stays `believed`; that is the
  ADR-0008 cold-start equivalence, not a gap to close here.
- Make R-mode regress persist agent observations to a NEW promotable event type
  in the name of an audit trail. Any new first-class observation event
  reintroduces the promotable path defect A exploited. Regress stays a read; it
  does not write promotable evidence.
- Route regress observations into the `CandidateEvent` stream. That stream is
  for agent-proposed risks and uncertainties (ADR-0014), not success
  confirmations; reusing it would mis-type the evidence and confuse review.

## Consequences

Positive:

- Regress can no longer self-certify the oracle. The believed success set is a
  function of seeds and genuinely independent-diverse evidence only; running
  regress N times over a seeded goal leaves the believed set unchanged.
- The `create-welcome-popup` inflation (4 seeded signals growing to 26
  agent-sourced ones, freezing the goal at UNCERTAIN) cannot recur: defect A no
  longer mints promotable summaries and defect B no longer promotes them.
- The oracle stays seeded-or-corroborated. A confidently-wrong single-agent
  paraphrase stays `contested` (the not-yet-trustworthy bucket), loud and
  visible to `praxis status` / `review`, never silently `believed`.

Negative:

- A regress run no longer leaves an `ObservationEvent` trail in the per-machine
  store. The verdict and the run record still capture what the run observed
  (the `RunResult` carries the observed signals and the report writers render
  them), but the confirmations are no longer folded into the append-only
  promotable history. This is the intended trade: a confirmation is not new
  knowledge, and persisting it as promotable evidence was the bug.
- The per-signal rule is slightly stricter than the goal-level flag, so a future
  caller that hand-builds summaries must give a lone agent summary a genuine
  different-type, different-source partner to see it `believed`; the goal-level
  flag alone will not do it.

Invariants respected:

- `first-oracle-must-be-seeded`: the believed set grows only from a human/spec
  seed or genuinely independent-diverse evidence; an agent confirmation run
  never promotes (ADR-0005, ADR-0008).
- `loud-and-traceable-over-silent-and-convenient`: a single-agent success
  summary stays `contested` and visible, never silently `believed`; regress
  reports its verdict loudly and does not quietly rewrite the oracle.
- `append-only-store-no-mutation`: nothing here mutates history. Defect A is
  fixed by NOT appending promotable evidence on a read, not by editing or
  deleting any prior event.

Invariants this ADR does NOT cover:

- The temporal mitigation of the INHERENT boundary is unchanged: a single
  fabricated different-type observation on a trusted seed is still `believed`
  until contradicted or it oscillates (ADR-0008). This ADR closes the
  unbounded same-type self-restatement vector, not the inherent single-genuine
  corroboration case, which remains mitigated over time and by the Phase-3
  governance layer.
- The recency-decay machinery (ADR-0013) is unchanged; promotion still runs over
  the FRESH surviving set and a staled signal lends no type/source to fresh
  claims.

## Relation to prior ADRs

Refines ADR-0005 (oracle trust by diversity, Accepted): the diversity-or-seed
gate is unchanged at the goal level; this ADR pins that the gate is applied
PER SUMMARY on the summary's own evidence, so a lone agent summary cannot borrow
the goal's seed-supplied independence. Same-type repeats still grant no
independence and a seed alone still believes from cold start.

Refines ADR-0008 (type-diversity needs source-independence, Accepted): keeps the
>=2-types / >=2-sources rule and the named INHERENT boundary; it closes the
remaining hole where the goal-level independence flag promoted an uncorroborated
lone-agent summary. The INHERENT single-genuine-corroboration case stays
`believed` and stays temporally mitigated. Does not supersede it.

Clarifies ADR-0009 (Phase 1 scope, regress reads the believed oracle, Accepted):
makes explicit that regress is a READ of belief and a WRITE of a verdict, never
a write of promotable belief. The regress runner stops persisting agent
observations as promotable `ObservationEvent`s; the verdict is still computed
in-memory exactly as ADR-0009 specified.

Does not supersede any prior ADR.
