# ADR-0011: Phase 2 scope, activated schema fields, and out-of-scope deferrals

Status: Proposed

## Context

ADR-0010 records the Phase 1 regression-recall gate as cleared
provisionally on `phase-1-r1`: memory beats `cold_readme` by +0.50
overall, +0.60 on knowledge-visible, +1.00 on the stale-trap category
at n=3, under the FP-rate guardrail and above the `off_path_fraction`
floor. ADR-0010 also names six residual caveats which become Phase 1.5
entry conditions.

`docs/phase-2-plan.md` enumerates six work areas (multi-writer
concurrency, recency decay, exploration incentive, real-app
generalization, Stagehand head-to-head, E-mode candidate persistence)
and a Phase 3 deferral list. This ADR is the Phase 2 mirror of
ADR-0009: it names what Phase 2 owns, defers the rest to a named
phase, and locks the Phase-1.5-vs-Phase-2 boundary item by item so
later ADRs in this batch do not relitigate it.

Phase-2 source items addressed here (per the brief): P2-02 (this ADR,
the Phase 2 scope umbrella), P2-20 (governance / RBAC / hosted shared
memory / dashboards, deferred), P2-21 (secret redaction beyond adapter
regex, deferred), P2-22 (poisoning detection beyond diversity-or-seed,
deferred), P2-23 (web UI, deferred), P2-24 (pricing / GTM, deferred).

The ADR-0009 schema-rot rule still binds: every Phase 2 field
activation has to be justified by experiment consumption.

## Decision

### 1. The five load-bearing Phase 2 work items

Phase 2 ships exactly these five, each with its own ADR later in this
batch:

1. **Multi-writer concurrency contract** (ADR-0012). Two or more
   agents append into the same store; contradictions surface as
   `contested`; oscillation as `quarantined`. Activates no new fields;
   the new surface is projection behavior under concurrent appends.
2. **Recency decay as projection-time derivation** (ADR-0013).
   Confidence drift is pure derivation; status flips emit explicit
   decay events. Activates `observed_app_version` (primary anchor)
   and wall-clock (secondary). The multi-writer-and-decay collision
   over which `observed_app_version` anchors the projection is decided
   in ADR-0013, not ADR-0012.
3. **E-mode candidate persistence as a sibling event type**
   (ADR-0014). Phase 2 persists candidate risks and uncertainties via
   a new `CandidateEvent` type with its own `schema_version`, sibling
   to (NOT an extension of) `ObservationEvent`. The Phase 1
   `risks.trigger` structured validator from ADR-0009 applies on write.
4. **Exploration reward, pre-registered before any optimization**
   (ADR-0015). Budget-weighted observability metric over existing
   `uncertainties` and `risks` events; does NOT feed agent state in
   Phase 2; adversarial Goodhart review gates any run.
5. **Real-app SUT selection plus the auth-state schema extension.**
   SUT selection is owned by ADR-0016 (Conduit recommended, Saleor
   fallback). The additive `auth_state: {authenticated: bool, scope:
   string|null}` field is owned by ADR-0017, deliberately separate
   from ADR-0016 because bundling SUT-pick with a PII-fragile schema
   extension is the canonical leak shape under porting pressure.

### 2. Schema activations under Phase 2

Each Phase 2 schema change is named here with its owning ADR and its
experiment consumer:

- `CandidateEvent` (new sibling event type): ADR-0014; consumed by
  `praxis review` and by the ADR-0015 reward numerator.
- `observed_app_version` as projection-time anchor: ADR-0013; consumed
  by the decay projection.
- `auth_state` additive field: ADR-0017; consumed by the ADR-0016
  port's R-mode and E-mode prompts. Must not carry tokens, cookies,
  user IDs, session IDs, or anything matching
  `no-secrets-tokens-pii-in-knowledge`; redaction stays at the adapter
  boundary.

Fields that stay deferred: `states` and `paths` remain deferred from
ADR-0009. No Phase 2 ADR in this batch activates them.

### 3. Phase 1.5 boundary, resolved item by item

Phase 1.5 owns and Phase 2 does NOT cover:

- **Stagehand adapter and head-to-head regression-recall experiment.**
  Deferred per ADR-0009 section 5; Phase 1.5 lands it with its own
  pre-registration.
- **Auditor protocol with a `refuted` status enum value.** Deferred
  per ADR-0009 section 6. Auditor scenarios continue as offline
  oracle-correctness checks; a `refuted` status requires diversity-
  or-seed over two independent failure detectors, which is Phase 1.5
  work. The four-value status enum (`believed` / `contested` /
  `stale` / `quarantined`) stays for Phase 2.
- **Cross-model API-key arm, n=5-with-prompt-variation, independent
  `cold_readme` re-authoring, t1/s1 release completion (`phase-1-r2`),
  judge re-grade sample.** Five ADR-0010 caveats; Phase 1.5 entry
  conditions.

Overlap resolution: ADR-0010 lists real-app generalization as a Phase
1.5 caveat AND `docs/phase-2-plan.md` lists it as a Phase 2
deliverable. This ADR resolves it: real-app generalization is OWNED
BY PHASE 2 (ADR-0016 plus ADR-0017) because the ADR-0012 adversarial
harness needs a non-toy surface; Phase 1.5 retains the five sibling
caveats.

### 4. Phase 3 deferrals, recorded here so no Phase 2 ADR has to

- Governance, RBAC, hosted multi-tenant shared memory, dashboards
  (P2-20).
- Secret redaction beyond the current adapter-boundary regex (P2-21).
  Phase 2 keeps adapter-boundary redaction as the rule (ADR-0009);
  Phase 3 adds layered redaction on top.
- Poisoning detection beyond the diversity-or-seed gate (P2-22).
  Phase 2 keeps ADR-0008 source-independence as the rule.
- Web UI (P2-23). CLI plus markdown reports continue through Phase 2.
- Pricing / GTM (P2-24). The moat is technical first.

### 5. Forbidden alternatives

DO NOT, in any Phase 2 ADR or implementation:

- Activate `states` or `paths` without a named ADR-owned experiment
  consumer (ADR-0009 schema-rot rule).
- Introduce a `refuted` status enum value in Phase 2; Phase 1.5 owns it.
- Bundle SUT selection (ADR-0016) with the schema extension (ADR-0017)
  in one ADR.
- Promote Phase 3 items into Phase 2 silently. Open a new ADR
  re-scoping if a Phase 2 experiment surfaces a hard need.
- Treat the Phase 1.5 caveats from ADR-0010 as Phase 2 blockers.

## Consequences

+ Phase 2 has a small, named, dependency-ordered work set; each item
  has an owning ADR later in this batch.
+ Schema activation stays justified field by field by experiment
  consumption, extending the ADR-0009 rule.
+ The Phase-1.5-vs-Phase-2 boundary is resolved once here; later ADRs
  reference ADR-0010 directly (Accepted) and reference this ADR for
  phase-boundary questions.
+ Phase 3 deferrals are recorded in one place; no Phase 2 ADR has to
  re-justify them.

- Adopting real-app generalization into Phase 2 means ADR-0012's
  multi-writer harness and ADR-0016's port land on roughly the same
  timeline. Intentional (multi-writer correctness needs a non-toy
  surface) but increases risk if the OSS SPA pick falls through.
  ADR-0016 names Saleor as fallback.
- Deferring Stagehand head-to-head to Phase 1.5 means Phase 2 ships
  without a moat-vs-procedural-cache result; the docs/06 existential
  risk stays open through Phase 2.
- ADR-0014 plus ADR-0015 together create Goodhart conditions.
  ADR-0015's adversarial review gate is the mitigation; this ADR
  records the coupling.

### Invariants respected by this ADR

- `operational-knowledge-not-procedures`: every Phase 2 work item is
  expressed as operational knowledge (signals, risks, uncertainties,
  candidates), not step procedures.
- `knowledge-not-mbt-procedure-cache`: Phase 2 scope excludes the
  Stagehand head-to-head and the action-cache experiment; the durable
  claim stays "what to test", not "how to drive the browser".
- `schema-is-single-source-of-truth`: every Phase 2 schema change is
  named here with an owning ADR; pydantic model and agreement test
  update with the schema in each owning ADR's implementation commit.
- `append-only-store-no-mutation`: multi-writer, decay, and candidate
  persistence are all event appends plus projection derivations; no
  in-place mutation at any Phase 2 boundary.
- `loud-and-traceable-over-silent-and-convenient`: Phase 1.5 / Phase 2
  / Phase 3 boundaries are named explicitly so a later contributor
  cannot silently absorb work across phases.

### Invariants explicitly NOT covered by this ADR

- `concurrent-writes-lose-no-knowledge`: owned by ADR-0012.
- `exploration-incentive-against-coverage-collapse`: owned by ADR-0015.
- `tenant-scoping-prevents-leakage`: owned by ADR-0012; hosted multi-
  tenant is Phase 3.
- `no-secrets-tokens-pii-in-knowledge`: owned by ADR-0017 for the
  Phase 2 auth-state field.
- `oracle-diversity-rule`, `no-self-corroboration-source-independence`,
  `first-oracle-must-be-seeded`, `provenance-and-confidence-mandatory`:
  inherited from ADR-0004 / ADR-0005 / ADR-0008 / ADR-0009; re-cited
  by ADR-0013 / ADR-0014 where they apply under Phase 2 multi-writer.

## Relation to prior ADRs

Extends ADR-0009 (Phase 1 scope) as its Phase 2 mirror: activates the
next field set, names the next experiments, defers the next set of
work, and locks the phase boundary.

Builds on ADR-0010 (Phase 1 verdict, Accepted). Phase 2 proceeds
because the moat survived the falsifier; the ADR-0010 caveats become
Phase 1.5 entry conditions, not Phase 2 blockers, per ADR-0010's
update path.

Does not supersede any prior ADR. ADR-0001 through ADR-0008 stay
binding into Phase 2 and are re-cited by later ADRs in this batch
where they apply.
