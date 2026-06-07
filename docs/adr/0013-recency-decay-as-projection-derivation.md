# ADR-0013: Recency decay as a projection-time event-driven demotion

Status: Proposed

## Context

The Phase 2 plan (item P2-04, see `docs/phase-2-plan.md` point 1) names
recency decay as load-bearing for multi-writer concurrency: "old beliefs
must not silently outvote new evidence". With ADR-0012 the store accepts
concurrent appends; without decay, a stale `believed` success oracle
attested by two diverse evidence types at app version v1.0.0 keeps masking
a real failure signal observed at v1.4.0, because the projection still
sees the older signals as valid corroboration.

ADR-0010 cleared the Phase 1 regression-recall gate at one app version
and surfaced the cost: most `believed` entries are anchored on a single
`observed_app_version`. Once Phase 2 introduces multiple versions and
multiple writers, the projection must demote those entries when their
supporting evidence ages out, or the moat decays into "the agent
confidently asserts last quarter's truth".

The hard constraint is doing this WITHOUT violating the append-only store
invariant (ADR-0001) and WITHOUT the silent confidence drift that ADR-0005
and ADR-0008 deliberately ruled out: a hidden float decaying toward zero
under the surface is exactly the "wrong but silent" failure mode AGENTS.md
flags as worse than no memory.

This ADR also fully owns the multi-writer / decay collision: when two
writers in ADR-0012's store disagree on `observed_app_version` for the
same logical signal, which version anchors the projection's
`current_version`. ADR-0012 stays silent on this per the approved
decomposition; the answer lives here.

## Decision

Decay is a property of the PROJECTION over the immutable event log, not a
mutation of events. The store never edits prior events; the projection
re-evaluates trust against a moving anchor and, when status flips, the
projection driver appends a new `decay_event` to the log so the demotion
is visible and traceable.

### 1. Confidence shifts are pure derivation; status flips append an event

Two distinct kinds of decay output:

- **Confidence derivation (no event).** The numeric `confidence` field
  on a projected signal is derived at projection time from underlying
  observation ages. No event is written for a confidence change alone.
  The projection function is pure: same event log, same anchor, same
  confidence.
- **Status flip (event written).** When recency decay causes the
  diversity-or-seed check to fail on the surviving non-staled set, the
  projected status transitions (`believed` -> `stale`, or `contested`
  -> `stale` if no live evidence is left contesting). The projection
  driver appends a `decay_event` referencing the retired event ids,
  the anchor used, and the rule that fired. Replaying the log
  reconstructs the same status without re-reading the clock.

This split keeps loud-and-traceable: a numeric confidence shift between
projections is noise the projection can recompute; a `believed` oracle
losing its corroboration is a status event the operator and the next
writer both need to see.

### 2. The diversity check runs over the SURVIVING set, not the historical set

Decay is a projection-time re-evaluation of the ADR-0008
`independent_diverse(...)` rule over the set of supporting signals NOT
staled by the decay function. Same-type repeats cannot keep a `believed`
status alive once the diverse signal has aged past threshold: if the only
remaining non-staled signal is one type from one source, the gate fails
and the status flips. Historical diversity is not credit.

This closes the obvious attack: stack ten same-type observations from the
same `agent_identity` at the current version to "refresh" a believed
status that lost its diverse evidence. `independent_diverse` already
rejects same-type stacking; this ADR makes the rejection survive across
time.

### 3. Decay function: observed_app_version primary, wall-clock secondary

A signal is "staled" if either pre-registered anchor crosses its threshold:

- **observed_app_version (primary).** Staled when `observed_app_version`
  is more than `N` minor versions behind the projection's
  `current_version` (default `N = 2`). Major-version changes stale every
  prior signal for the affected goal. Semver-shaped comparison; non-semver
  tags fall through to wall-clock.
- **wall-clock (secondary).** Staled when the observation timestamp is
  more than `T` days behind run-start (default `T = 90`). Catches apps
  that never bump a version string and the rare case where version
  metadata is missing.

`N` and `T` are pre-registered per experiment and pinned by `praxis_git_sha`
in the run manifest (same convention as the sigma-bounded kill gates in
`docs/phase-1-experiment.md`). Changing either invalidates prior data,
per ADR-0009.

### 4. Decay is unidirectional; re-promotion requires new diverse evidence

A signal staled by decay does not un-stale if a later write happens to
agree. Re-promotion from `stale` to `believed` requires fresh evidence
that, on its own (without the retired signals), passes the
`independent_diverse(...)` gate: at least two distinct `source_id`s AND
at least two distinct evidence types, all non-staled. Same gate as
ADR-0008 cold start.

This forbids the loop where a same-type writer tops up a decaying oracle
every 89 days to keep it alive forever. The store does not forbid the
appends; the projection does not count them as new diversity.

### 5. Multi-writer / decay collision: current_version is a projection input

When two writers in ADR-0012's store record different `observed_app_version`
values for signals contributing to the same projected entry, the projection
picks a single `current_version` per this rule, in order:

1. `current_version` is an input to the projection call from the caller
   (the runner via the adapter; CLI `praxis status` passes it explicitly,
   `praxis regress` reads it from the active `KnowledgeAdapter`'s app
   context). The projection does NOT pick a version from the log.
2. If the caller passes nothing, the projection uses the highest-semver
   `observed_app_version` present in the supporting set. Highest-semver
   is deterministic and monotonic across concurrent writers (independent
   of write order), so two parallel writers cannot flip the projection
   back and forth.
3. If no event in the supporting set has a semver-shaped version, fall
   through to wall-clock-only decay using the projection's run-start
   timestamp.

Anchor selection lives at the boundary between the runner and the
projection, NOT inside the store. The store stays version-agnostic
(ADR-0012 store-layer contract holds). Writers disagreeing on
`observed_app_version` produce no race: the projection re-derives the
anchor deterministically every time.

### Forbidden alternatives

DO NOT do this:

- **No in-place confidence mutation.** The `confidence` field on a stored
  event is never edited. Projected confidence is derived; event confidence
  is the writer's at-write-time provenance and stays frozen.
- **No silent status drift.** A decay-driven status transition MUST emit
  a `decay_event` referencing retired event ids and the anchor used. A
  projection that flips `believed` to `stale` without writing an event
  has erased the audit trail.
- **No same-type repeats keeping `believed` alive.** Ten fresh same-type
  observations from the same `source_id` cannot prevent a decay-driven
  status flip; the re-evaluation runs against the surviving set.
- **No reversible decay.** A `stale` signal is never silently re-promoted
  because a later write agrees. Re-promotion goes through the ADR-0008
  cold-start gate.
- **No store-level `observed_app_version` write-lock.** Writers record
  whatever version they observed; the projection, not the store, decides
  which version anchors decay.
- **No per-run decay thresholds.** `N` and `T` are pre-registered per
  experiment and pinned by `praxis_git_sha`; a writer cannot pass custom
  thresholds at projection time to coax a flip.

## Consequences

### Positive

+ Decay never edits prior events; the append-only contract from ADR-0001
  and ADR-0012 holds without exception.
+ Status flips are visible: `praxis status` and `praxis review` see the
  `decay_event` as a first-class entry, so an oracle that quietly retired
  yesterday cannot mislead today's runner.
+ The ADR-0005 / ADR-0008 diversity rule survives time: a `believed`
  oracle stays believed only while its diverse supporting set stays
  non-staled. Same-type stacking cannot defeat decay.
+ The multi-writer / decay collision has a deterministic,
  write-order-independent answer; concurrent writers observing different
  versions do not produce projection oscillation.
+ Re-promotion via the ADR-0008 cold-start gate keeps a forgery from
  reviving a retired oracle by repetition.

### Negative

- Projection cost grows with event log size: every pass re-evaluates the
  decay function for the supporting set. Phase 2 scale is fine; hosted
  Phase 3 scale may need an incremental anchor cache (a derived structure,
  not a mutation; out of scope here).
- Thresholds `N = 2` and `T = 90` are calibrated against Phase 1's
  testapp. The real-app port is the first place they face real version
  cadence; the pre-registration constraint means re-tuning invalidates
  prior runs.
- A correct seed for an oracle that no current app version still exposes
  decays to `stale` if no agent re-observes it in time. Intended behavior
  (the claim is no longer falsifiable against the running app), but
  `praxis review` must surface decay-driven flips clearly so first-time
  users are not surprised.

### Invariants this ADR respects

- `append-only-store-no-mutation`: confidence is derived; status flips
  append a `decay_event` referencing retired event ids.
- `oracle-diversity-rule`: the ADR-0005 / ADR-0008 gate is re-evaluated
  over the surviving non-staled set at projection time.
- `no-self-corroboration-source-independence`: same-type repeats from
  the same `source_id` cannot keep `believed` alive after diverse
  evidence stales.
- `loud-and-traceable-over-silent-and-convenient`: every decay-driven
  transition leaves a `decay_event`; no silent drift.
- `contradictions-preserved-as-contested`: decay never converts
  `contested` to `believed`; it only flips to `stale` once the
  contested supporting set ages out.
- `concurrent-writes-lose-no-knowledge`: the multi-writer / decay
  collision resolves by deterministic anchor selection, not serializing
  writers.
- `provenance-and-confidence-mandatory`: `decay_event` entries carry
  their own provenance (projection driver, retired event ids, anchor).
- `knowledge-not-mbt-procedure-cache`: decay applies to operational
  knowledge, never to a step cache.

### Invariants this ADR does NOT cover

- `tenant-scoping-prevents-leakage`: ADR-0012 covers the store-layer
  piece; the adapter-boundary piece is owned elsewhere in the Phase 2
  ADR series.
- `exploration-incentive-against-coverage-collapse`: owned elsewhere
  in the Phase 2 ADR series.
- `no-secrets-tokens-pii-in-knowledge`: adapter-boundary concern
  (ADR-0003 plus a later Phase 2 ADR), unrelated to decay.

## Relation to prior ADRs

- Extends ADR-0001 by adding `decay_event` as a new event kind. Store
  contract unchanged; the projection knows one more event type.
- Refines ADR-0005 and ADR-0008 by extending `independent_diverse(...)`
  to a time-windowed surviving set. Neither ADR is reversed; both now
  evaluated over a moving window rather than over the full historical
  set.
- Depends on ADR-0012 for `source_id = agent_identity` semantics and for
  the multi-writer store layout the projection runs against. ADR-0012
  stays silent on the decay collision per the approved decomposition;
  this ADR owns it.
- Builds on ADR-0009 for the pre-registration convention (thresholds
  pinned by `praxis_git_sha`) applied to `N` and `T`.
- Builds on ADR-0010 (Accepted), which named single-version exposure as
  a residual risk; this ADR is the Phase 2 answer.
