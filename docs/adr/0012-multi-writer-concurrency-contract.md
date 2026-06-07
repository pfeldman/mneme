# ADR-0012: Multi-writer concurrency contract: append-only safety, source-independence under contention, and day-one adversarial harness

Status: Accepted (2026-06-07)

## Context

Phase 1 validated one writer against a controlled SUT (ADR-0010). Phase 2
introduces the load-bearing multi-writer property: two or more agents
writing into the same store at the same time. ADR-0011 names this as the
first of the five Phase 2 work items and points at three phase-2 source
items this ADR has to resolve:

- P2-03: concurrent appends must not lose events; the projection over
  those appends must be deterministic.
- P2-05: contradiction surfaces as `contested`, oscillation as
  `quarantined`; multi-writer must not weaken the diversity-or-seed gate
  (ADR-0005, ADR-0008).
- P2-19: multi-writer correctness is the hardest module; if it goes
  silent, poisoning goes with it (docs/06). The adversarial harness has
  to exist on day one, not as a post-hoc check.

Two failure shapes are in scope: a store-layer shape ("appends race and
one is lost", or "the projection depends on which append the reader sees
first") and a gate-layer shape ("N agents of the same model count as N
independent sources and self-promote"). ADR-0008 named the second as a
poisoning vector for single-writer; under multi-writer load it becomes
the dominant failure mode if `source_id` is set carelessly. Phase 1 set
`source_id = agent_identity` (model plus prompt lineage), not `run_uuid`
(ADR-0009 section 3); this ADR records that choice as the multi-writer
contract.

Phase 2 also has to declare tenancy at the store layer; shipping a multi-
writer store with no tenancy contract is the canonical cross-tenant leak.
Hosted multi-tenant is Phase 3 (ADR-0011); Phase 2's job is a single-
tenant-by-contract boundary so Phase 3 extends without retrofitting. The
multi-writer-and-decay collision on `observed_app_version` is out of
scope here and will be addressed in a subsequent ADR.

## Decision

The Phase 2 multi-writer contract has two named sections plus one named
delivery clause. All three are load-bearing.

### 1. Store-layer contract: append-only file layout as the lock-free mechanism

Concurrent writers operate against the same store root via a file-per-event
layout. Each event lands as its own file, named by a content-addressable
event id that incorporates the writer's `agent_identity`, the event type,
and a high-resolution timestamp. The filesystem rename is the commit point;
event id collisions are impossible by construction. Projection is a
deterministic fold over the sorted union of events present at read time;
two readers reading the same on-disk set get the same projection, and a
reader that races with a writer never observes a partial event.

Forbidden alternatives:

```
DO NOT: last-write-wins on a single mutable file.
DO NOT: in-place mutation of an event after it lands.
DO NOT: a shard-merge step whose output depends on scan order.
DO NOT: a consensus synthetic entry that aggregates several writers'
        observations into one event and drops per-source provenance.
```

The "no consensus synthetic entries" clause is the subtle one: folding N
agents' agreeing observations into one signal destroys the source-
independence ADR-0008 needs. Each writer's observation persists as its own
event with its own `source_id`; the projection counts sources at read time,
not at write time.

### 2. Gate-layer contract: source-independence under contention

`source_id` is `agent_identity` (model plus prompt lineage), NEVER
`run_uuid`, `session_id`, or any per-process identifier. Consequences:

- N concurrent writers of the same model count as one source under
  ADR-0008's `independent_diverse(...)`. Same-model self-promotion is
  structurally impossible regardless of writer count.
- Two writers of different `agent_identity` whose observations contradict
  produce a `contested` projection, not a merged one (ADR-0001).
- Oscillation across writers produces `quarantined` (ADR-0005). The
  quarantine status is derived from the event set, not a flag mutated on
  the underlying events.

Forbidden alternatives:

```
DO NOT: source_id = run_uuid, session_id, pid, or any per-process token.
DO NOT: source_id derived from hostname or worker index.
DO NOT: a "fast path" that bypasses independent_diverse(...) when
        several writers agree within a short time window.
DO NOT: a confidence boost based on the count of agreeing writers.
```

### 3. Single-tenant-by-contract placeholder

The Phase 2 multi-writer store is single-tenant by contract. The file_store
boundary enforces a per-store-root path convention so Phase 3 hosted
multi-tenant is an extension, not a retrofit. Shape:

```
store/<tenant_id>/events/<event_id>
```

`<tenant_id>` is required by the file_store API on every write and read.
A write that omits it is rejected at the adapter boundary; a read across
`<tenant_id>` values is explicitly UNSAFE and is not exposed by the
file_store SPI. Phase 2 ships with one tenant id per deployment (`local`
is the conventional default).

Forbidden alternatives:

```
DO NOT: a flat store root with tenant id as a field inside the event body.
DO NOT: a cross-tenant projection API ("read events for ANY tenant").
DO NOT: a Phase 3 retrofit that moves tenant_id from path to metadata
        without superseding this ADR.
```

The path-prefix convention is a placeholder, not a security boundary;
Phase 3 supersedes it with RBAC and governance. Until then, the
convention closes the "I forgot to scope the read" footgun by making
cross-tenant reads representable only via an SPI surface that does not
exist.

### 4. Adversarial harness ships in the same commit as multi-writer store changes

This is a delivery clause on the multi-writer work, raised to a named
decision section because P2-19 says multi-writer correctness is the load-
bearing module and silent poisoning (docs/06) is the failure shape we
cannot tolerate.

A new `experiments/multi_writer/` directory ships in the same commit as
the file_store changes. It contains, at minimum:

- A concurrent-append scenario asserting no event is lost under N writers.
- A same-`agent_identity` parallel-writer scenario asserting the
  diversity-or-seed gate is not satisfied no matter how many writers
  agree on the same single-type signal (ADR-0008 attack under contention).
- A contradiction-under-contention scenario asserting `contested`, not
  last-write-wins.
- An oscillation-across-writers scenario asserting `quarantined` per
  ADR-0005.
- A cross-tenant-write scenario asserting the file_store rejects the
  write at the boundary.

The CI gate refuses to merge a PR that touches the multi-writer store
without the corresponding scenarios under `experiments/multi_writer/`.

Forbidden alternatives:

```
DO NOT: ship store changes in commit N and the harness in commit N+1.
DO NOT: rely on single-writer oracle_stress.py to cover multi-writer.
DO NOT: stub the harness with a TODO and merge anyway.
```

## Consequences

Positive:

- Concurrent writes cannot lose knowledge: rename is the commit point and
  projection determinism is independent of interleaving.
- Same-model self-promotion is structurally impossible under load.
- Contradiction and oscillation surface as data (`contested`,
  `quarantined`), preserving the ADR-0001 append-only contract.
- The store ships with a tenancy contract from day one; Phase 3 extends.
- The adversarial harness lands with the change it stresses; the CI gate
  makes "we forgot the test" unrepresentable.

Negative:

- Per-event files inflate the on-disk file count vs a single append-log.
  Acceptable in Phase 2; a Phase 3 hosted deployment may need a packed-
  segment representation behind the file_store SPI without changing the
  contract.
- The single-tenant clause is a path convention, not a permissions
  boundary. A misconfigured deployment is a bug Phase 3 catches.
- The CI gate adds a hard merge dependency between store work and the
  harness. Acceptable; the cost of silent poisoning is higher.

Invariants respected:

- append-only-store-no-mutation: file-per-event, no in-place edits, no
  consensus synthetic merge that drops per-source provenance.
- concurrent-writes-lose-no-knowledge: rename as commit point;
  projection is a deterministic fold; the harness asserts this directly.
- no-self-corroboration-source-independence: `source_id = agent_identity`;
  N concurrent same-model writers count as one source under ADR-0008.
- contradictions-preserved-as-contested: contradiction under contention
  yields `contested`, not last-write-wins.
- tenant-scoping-prevents-leakage: per-store-root path convention
  enforced at the file_store boundary; cross-tenant reads are not in
  the SPI surface.
- loud-and-traceable-over-silent-and-convenient: the harness ships in
  the same commit and the CI gate refuses merges that skip it.

Invariants this ADR does NOT cover:

- exploration-incentive-against-coverage-collapse: not covered by this
  ADR.
- no-secrets-tokens-pii-in-knowledge: not covered by this ADR. The
  tenancy clause here is structural isolation, not redaction.

## Relation to prior ADRs

- Extends ADR-0001 (append-only event log): file-per-event is the
  multi-writer realization of the append-only contract.
- Refines ADR-0008 (type-diversity needs source-independence): restates
  source-independence as a multi-writer invariant and binds `source_id =
  agent_identity` so concurrent same-model writers cannot satisfy
  `independent_diverse(...)` by count.
- Extends ADR-0005 (oracle trust by evidence diversity): `contested` and
  `quarantined` paths under contention are the same paths as the single-
  writer case, applied without weakening.
- Builds on ADR-0009 section 3 (E-mode `source_id` rule), promoting it
  from a runner detail to a multi-writer contract.
- References ADR-0010 (Phase 1 verdict Accepted) as the precondition for
  starting Phase 2 work.
