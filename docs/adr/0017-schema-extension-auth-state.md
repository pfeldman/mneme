# ADR-0017: Schema additive extension: auth_state projected field

Status: Proposed

## Context

ADR-0016 selects a real application (Conduit, with Saleor as fallback)
as the Phase 2 system under test, replacing `experiments/ui-mutation/testapp.py`.
The Phase 1 four goals (login, search, checkout, admin_access) ported
into the new SUT surface a projected fact the Phase 1 schema does not
name: an agent must reason about whether the current session is
authenticated and, if so, at what scope (`anonymous`, `user`, `admin`).
The ADR-0009 schema encodes this implicitly through success signals on
protected endpoints. That worked for `testapp.py`; on a real SUT,
multiple goals share the same auth precondition, and re-inferring it
from per-signal evidence inflates token cost and obscures cross-goal
reuse.

P2-10 (port the four-goal regression-recall harness onto a real SUT)
has two halves: SUT selection and the schema delta needed to make the
port honest. ADR-0016 owns the first. This ADR owns the second.

This ADR is intentionally separate from ADR-0016. Bundling a SUT pick
with a PII-fragile schema extension is the canonical leak shape: under
porting pressure, an author reaching for "what does this app expose"
tends to capture tokens, cookies, user IDs, session IDs, or PII as
durable knowledge, because they were available in the trace at write
time. ADR-0009 rejected free-text triggers for the same
write-time-loudness reason. Keeping the schema decision in its own
document forces the no-secrets contract to be reviewed on its own
merits, not as a footnote of a SUT pick.

Phase 1 sealed artifacts (`schema/knowledge.schema.json`, the pydantic
model under `src/praxis/model/`, the model-schema agreement test, the
adapter SPI) are untouched here. The change is doc-only; the
implementing commit lands in Phase 2.

## Decision

Add `auth_state` as an additive projected field on the knowledge
schema, shaped to encode only the abstract authentication posture and
never the credentials that produced it. The adapter remains the
redaction point; the schema is the contract that says what is allowed
to cross the boundary in the first place.

### 1. Projected shape

`auth_state` is a projected field on the per-goal knowledge surface,
with exactly two subfields:

```
auth_state: {
  authenticated: bool,
  scope: string | null
}
```

`authenticated` is derived from observable behavioral and network
signals (a 200 on a known-protected endpoint, a post-login DOM
affordance, a successful `/me` response). `scope` is the abstract
role the projection believes the session occupies (`anonymous`,
`user`, `admin`, or a SUT-specific role string registered in the
knowledge file). `scope` is `null` when `authenticated` is `false`
or when the surviving evidence is too thin to claim a scope under
the ADR-0008 diversity-or-seed rule.

`auth_state` is produced by the projection over existing
`success_signals` and `failure_signals`. It is NOT a new oracle:
promotion reuses `oracle/trust.py` `independent_diverse(...)` over
the underlying signals exactly as ADR-0008 specifies. No new code
path, no new diversity rule, no new status enum.

### 2. What `auth_state` MUST NOT carry

The schema rejects, at write time, any of the following as values
inside `auth_state` or in observations that project into it:

- Access tokens, refresh tokens, API keys, bearer strings.
- Cookies (raw or parsed) and cookie names that double as session IDs.
- User identifiers (`user_id`, `account_id`, email, username).
- Session identifiers (`session_id`, `sid`, JWT contents).
- Personally identifiable information of any kind.
- Any field whose value is generated per-session or per-user rather
  than per-app-version.

A validator at the adapter boundary, sibling to the `risks.trigger`
validator from ADR-0009, rejects observations that attempt to write
any of the above into the auth-state surface. Rejection is loud: a
traceable event is emitted, and the offending observation does not
enter the store.

### 3. Redaction stays at the adapter boundary

The adapter (Phase 2: an HTTP client wrapping Conduit, eventually
Browser Use / Stagehand) continues to be the redaction point. Adapters
strip cookies, tokens, and PII from raw responses before constructing
the observation that calls `write_observations`. Redaction at the
adapter is the runtime defense; the schema is the contract that says
what redaction is for. A new adapter author cannot accidentally widen
the surface by adding fields the schema rejects.

### 4. Same-commit update of schema, model, and agreement test

When the Phase 2 implementation lands `auth_state`, three artifacts
move together in one commit: `schema/knowledge.schema.json` (the
`auth_state` object plus rejection of forbidden field names),
the pydantic model under `src/praxis/model/` (matching `AuthState`
with a validator rejecting forbidden values), and the model-schema
agreement test (extended to cover `auth_state` so drift fires at CI).
A commit that updates one without the others is rejected in review.

### 5. Forbidden alternatives

DO NOT do this: store the bearer token as part of `auth_state` "for
debugging".

DO NOT do this: store the cookie name and value to "reproduce the
session later".

DO NOT do this: store the `user_id` as the `scope` value.

DO NOT do this: extend `auth_state` with a `tenant_id`, `org_id`, or
`workspace_id` field. Tenant-scoping is path-level (see ADR-0012
single-tenant-by-contract path convention), not a knowledge surface.

DO NOT do this: bundle the `auth_state` schema landing into the
ADR-0016 SUT-selection commit. Schema changes that touch the
no-secrets invariant land in their own commit, with their own review,
alongside the model and agreement-test updates.

DO NOT do this: encode authentication posture as free-text inside a
`success_signal.summary` to dodge the schema validator. The validator
applies to projected fields and to observations that feed them.

## Consequences

+ The schema explicitly names the auth surface and rejects, at write
  time, the field shapes that would leak credentials or PII into
  durable knowledge. Wrong writes become loud at the boundary
  (no-secrets-tokens-pii-in-knowledge, loud-and-traceable-over-silent-and-convenient).
+ Cross-goal sharing of authentication posture becomes a first-class
  projection, not per-signal re-derivation. Phase 2 multi-goal runs
  on Conduit avoid paying the auth-inference token cost per goal.
+ The adapter SPI stays at its two methods (`read_knowledge`,
  `write_observations`); the new field is a model and schema change,
  not a new SPI call (adapter-spi-tiny-and-stable).
+ Schema and pydantic stay in lockstep via the existing agreement
  test (schema-is-single-source-of-truth).
+ ADR-0008 source-independence applies unchanged: a single adapter
  cannot self-promote an authenticated posture across types without
  a second independent source or a seed
  (no-self-corroboration-source-independence).

- One more field to maintain. Justified by P2-10 SUT-shaped need,
  not theoretical completeness; if the Conduit port surfaces that
  auth_state is rarely read by R-mode or E-mode, the field gets
  demoted to deferred in a follow-up ADR (same field-by-field
  discipline as ADR-0009).
- Adapter authors carry the redaction burden. A new adapter that
  forgets to strip cookies before `write_observations` is caught by
  the boundary validator, but the validator is a backstop; the
  primary contract is at the adapter layer.
- Concurrency is NOT covered here. Two writers updating `auth_state`
  for the same goal under contention follow ADR-0012 and ADR-0013.
- Tenant-scoping is NOT covered here. Cross-tenant isolation is the
  path convention in ADR-0012; the schema does not encode tenant
  identity as a field.
- Candidate `auth_state` (an E-mode candidate proposing a new scope)
  is NOT covered here. That follows ADR-0014's CandidateEvent path;
  this projection applies to promoted signals only.

### Invariants respected

- schema-is-single-source-of-truth
- no-secrets-tokens-pii-in-knowledge
- invariants-not-coordinates-hierarchy (auth posture is abstract)
- adapter-spi-tiny-and-stable
- loud-and-traceable-over-silent-and-convenient
- provenance-and-confidence-mandatory (inherited from underlying signals)
- append-only-store-no-mutation (auth_state is a projection)

### Invariants explicitly NOT covered by this ADR

- concurrent-writes-lose-no-knowledge (owned by ADR-0012)
- tenant-scoping-prevents-leakage (owned by ADR-0012 path convention)
- exploration-incentive-against-coverage-collapse (owned by ADR-0015)
- oracle-diversity-rule (reused unchanged from ADR-0008, not refined here)

## Relation to prior ADRs

Extends ADR-0002 (schema as neutral interop layer): adds `auth_state`
as an additive projected field with no wire-protocol implications.
Refines ADR-0003 (runtime adapter boundary): codifies that the adapter
is the redaction point and the schema defines what may cross. Depends
on ADR-0016 (real-app SUT selection): the auth_state need is surfaced
by porting the four Phase 1 goals into Conduit. Inherits ADR-0008
(oracle source-independence) unchanged. Inherits ADR-0009 (validator
pattern): the auth_state field validator follows the `risks.trigger`
validator shape. Does not supersede any prior ADR.
