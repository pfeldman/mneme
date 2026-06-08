# ADR-0014: E-mode candidate persistence as sibling CandidateEvent

Status: Accepted (2026-06-07)

## Context

Phase 1 (ADR-0009) shipped E-mode (`src/praxis/runner/exploration.py`)
with two output channels: candidate signal observations (which DO flow
through `adapter.write_observations` and the existing oracle gate today)
and new candidate risks + uncertainties (which currently survive only
inside the `ExplorationResult` returned to the caller). ADR-0009 records
the latter explicitly under "Open gap recorded for Phase 1.5": the
projection has nothing new to fold in, so `praxis review` cannot surface
agent-written candidates across sessions. Phase 2 plan items P2-17
(candidate persistence) and P2-18 (`praxis review` consumes them)
reopen that gap as load-bearing Phase 2 work. ADR-0010 cleared the
Phase 1 regression-recall gate, so further E-mode investment is settled
by a real number, not a hedge.

The naive fix - widen `ObservationEvent` to carry candidate risks and
uncertainties - is the wrong move. `ObservationEvent` is the wire shape
the oracle gate (`oracle/trust.py`, `independent_diverse`) reads as
evidence-of-a-signal; bundling agent-proposed risks into the same
envelope would conflate "agent observed signal X" with "agent thinks
risk Y exists", and the diversity gate would silently start counting
risk-proposals as signal observations. ADR-0008 hardened the gate
against this kind of shape drift. A sibling type also lets the candidate
payload evolve its own `schema_version` without disturbing the schema-
pydantic agreement test for signal events or invalidating prior events
under ADR-0001's append-only contract.

## Decision

### 1. CandidateEvent is a NEW sibling event type, not an extension of ObservationEvent.

A separate event type `CandidateEvent` lives alongside `ObservationEvent`
in `praxis.store.events` with its own `schema_version` and one of two
payload variants:

- `candidate_risk`: the same fields a seeded `risks` entry carries
  (description, `trigger`, provenance, confidence), authored by an
  E-mode agent rather than by a human/spec seed.
- `candidate_uncertainty`: the same fields a seeded `uncertainties`
  entry carries (description, author, timestamp), authored by an E-mode
  agent.

`ObservationEvent` stays signals-only. Existing signal observations from
E-mode continue to flow through `adapter.write_observations` unchanged;
they are NOT candidates under this ADR.

### 2. Default status is `contested`. Promotion uses the SAME diversity rule.

Every CandidateEvent enters the projection with status `contested`. There
is no relaxed promotion path. Promotion to `believed` requires the SAME
`independent_diverse(...)` rule `oracle/trust.py` enforces today: at
least two distinct `source_id`s AND at least two distinct evidence
types in agreement. Phase 1 (ADR-0009) sets `source_id = agent_identity`
(model + prompt lineage), NOT `run_uuid`; that rule is inherited
unchanged. Two writers of the same `agent_identity` with same-type
candidate observations are ONE source under ADR-0008's source-
independence hardening, so N concurrent same-model E-mode runs under
multi-writer concurrency (ADR-0012) cannot self-promote their own
candidates. The residual case (seed plus a single different-type agent)
remains the ADR-0008 INHERENT trust boundary, mitigated temporally.

### 3. `risks.trigger` structured validator applies on write.

A `candidate_risk` MUST carry a structured `trigger` in one of the two
forms ADR-0009 section 4 accepts (HTTP method-path-body-predicate, or
sequence-action-postcondition). Free-text triggers are REJECTED at the
adapter boundary; borderline cases produce an LLM-judge event (loud,
traceable). A rejected candidate does not enter the store. Provenance
and confidence (ADR-0004) are mandatory on `candidate_risk`; author and
timestamp are mandatory on `candidate_uncertainty`.

### 4. `praxis review` surfaces a queue of contested candidates with provenance.

`praxis review` reads the projection's contested candidates (both kinds)
and presents them with full provenance: `agent_identity`, run id,
`observed_app_version`, and any subsequent corroborating or contradicting
events. The reviewer has three options per candidate: (a) promote by
writing a NEW seed event (the original CandidateEvent is NEVER edited;
seed + candidate = two independent sources and the existing gate handles
promotion under ADR-0001); (b) leave contested (the candidate stays in
the queue and is re-surfaced next review); (c) discard via decay (the
reviewer does not delete; a decay event is appended per (5)).

### 5. Unresolved candidates decay to `stale`, never to silent removal.

CandidateEvents are subject to the recency-decay rule in ADR-0013. A
candidate that no diverse evidence corroborates within the decay window
flips from `contested` to `stale` via an explicit decay-event append,
never via silent in-place mutation. Re-promotion requires fresh diverse
evidence; decay is unidirectional.

### Forbidden alternatives

DO NOT widen `ObservationEvent` with optional `candidate_risk` or
`candidate_uncertainty` fields. CandidateEvent is a sibling type.

DO NOT introduce a relaxed promotion rule for candidates (single-source,
count-based, or automatic-after-N-same-source). The Phase-1
`independent_diverse(...)` rule applies unchanged.

DO NOT edit a CandidateEvent in place on human promotion. Promotion
appends a NEW seed event; the original candidate remains immutable.

DO NOT silently remove unresolved candidates. Decay to `stale` (via an
appended decay event) is the only path off the contested queue without
promotion.

DO NOT set `source_id = run_uuid` (or any per-run identifier) for
candidate writes. The runner sets `source_id = agent_identity` per
ADR-0009 so N parallel same-model E-mode runs cannot self-promote.

DO NOT carry tokens, cookies, user IDs, session IDs, or any other
secret/PII payload into a CandidateEvent. The adapter-boundary
redaction rule applies as for ObservationEvent.

## Consequences

Positive:

- `praxis review` finally has something to review across sessions. The
  Phase-1 gap ADR-0009 named is closed without weakening the oracle
  gate: agent-proposed risks and uncertainties become first-class
  durable knowledge only through the SAME diversity-or-seed promotion.
- The schema-rot defense ADR-0009 closed for `risks.trigger` extends to
  agent-authored triggers: a hallucinating E-mode agent producing "race
  condition under load" gets REJECTED at the boundary, not silently
  believed after enough same-source repeats.
- The `ObservationEvent` shape stays narrow and signals-only.
  CandidateEvent evolution happens in its own `schema_version`.
- Human promotion via a fresh seed event preserves ADR-0001 end to end:
  the audit trail shows agent-proposed-then-human-seeded as two
  distinct events; the projection's status change is derivable from
  the log alone.

Negative:

- A second event type is more surface for the projection to handle (the
  SPI itself stays at `read_knowledge` / `write_observations` per
  ADR-0003).
- `praxis review` becomes a real human-in-the-loop bottleneck. If
  E-mode produces candidates faster than reviewers act, the contested
  queue grows unbounded; ADR-0013 decay caps the blast radius, but
  human-intervention-rate becomes a Phase 2 metric to watch.
- The ADR-0008 INHERENT boundary extends to candidates: a single E-mode
  agent proposing a candidate risk that an existing seed corroborates
  can still ride to `believed`, mitigated temporally, not at promotion
  time.

Invariants respected:

- `provenance-and-confidence-mandatory`: every CandidateEvent carries
  provenance + confidence (risks) or author + timestamp (uncertainties);
  the write path rejects entries that lack them.
- `append-only-store-no-mutation`: candidates are never edited; human
  promotion appends a new seed event; decay appends a decay event.
- `no-self-corroboration-source-independence`: `source_id =
  agent_identity` means N same-model E-mode runs count as one source.
- `oracle-diversity-rule`: candidate promotion uses
  `independent_diverse(...)` unchanged.
- `schema-is-single-source-of-truth`: CandidateEvent gets its own
  `schema_version` in schema + pydantic with the agreement test
  enforcing parity.
- `loud-and-traceable-over-silent-and-convenient`: free-text triggers
  rejected with LLM-judge events; decay and promotion both append
  explicit events.
- `contradictions-preserved-as-contested`: candidates default to
  `contested` until diverse evidence promotes or decay flips them.
- `knowledge-not-mbt-procedure-cache`: candidates carry operational
  knowledge, not click-by-click procedures.
- `no-secrets-tokens-pii-in-knowledge`: CandidateEvent inherits the
  adapter-boundary redaction rule.

Invariants this ADR does NOT cover:

- `exploration-incentive-against-coverage-collapse`: how E-mode is
  rewarded for producing candidates is OUT OF SCOPE for this ADR; a
  separate exploration-reward ADR (to be authored in this batch) owns
  it. This ADR only persists what E-mode emits; it does not change the
  reward function.
- `tenant-scoping-prevents-leakage`: cross-tenant isolation of
  CandidateEvent storage rides ADR-0012's single-tenant-by-contract
  placeholder.
- `concurrent-writes-lose-no-knowledge`: the multi-writer guarantees
  for CandidateEvent are inherited from ADR-0012's store-layer and
  gate-layer contracts; this ADR does not re-derive them.

## Relation to prior ADRs

Extends ADR-0001 (append-only event log) by adding a new event type
without touching the immutability contract. Extends ADR-0009 by closing
the "Open gap recorded for Phase 1.5" for agent-written candidate risks
and uncertainties. Refines ADR-0008 by re-applying the source-
independence rule to a new event type and carrying the INHERENT
boundary forward. Depends on ADR-0012 for the multi-writer source_id
semantics under contention and ADR-0013 for the decay rule that retires
unresolved candidates.
