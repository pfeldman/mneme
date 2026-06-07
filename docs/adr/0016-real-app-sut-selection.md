# ADR-0016: Real-app SUT selection: pre-registered criteria and Conduit recommendation

Status: Proposed

## Context

Phase 1 (ADR-0009, ADR-0010) ran its regression-recall falsifier against
`experiments/ui-mutation/testapp.py`, a plain `http.server` app authored
in the same repo by the same hand that wrote the planted-regression
manifest. ADR-0010 accepted the Phase 1 verdict provisionally and named
real-app generalization as one of the four Phase 1.5 entry conditions
before the moat claim is considered settled. Phase 2 plan items P2-08,
P2-09, and P2-10 lift this from caveat to load-bearing deliverable: pick
one OSS app, port the regression-recall machinery, and re-run with
multi-writer seeds.

Two failure modes shape the decision. First, picking the SUT after
looking at candidate flow surfaces is the classic Goodhart vector:
criteria written after a candidate is favored fit the candidate, not
the claim. Second, bundling SUT selection with schema changes is the
canonical porting-leak shape; the Phase 2 plan flags an expected
additive schema extension around session / auth-state. Per the brief,
that schema decision lives in ADR-0017, not here. Phase 1 sealed
artifacts underwrite ADR-0010's provisional clearance and must not be
edited by the port.

## Decision

### 1. Pre-registered selection criteria

A candidate SUT qualifies only if it meets all four, ordered by priority:

1. **Public and dockerizable in under 30 minutes on a developer laptop.**
   Single `docker compose up` (or equivalent) reaches a running app with
   pre-loaded demo data on commodity hardware in 30 minutes wall time
   including image pull. No paid SaaS, no required cloud accounts, no
   manual DB seeding step exceeding 5 minutes.
2. **At least three goal-shaped flows comparable to the Phase 1 four
   (`login`, `search`, `checkout`, `admin_access`).** A goal-shaped flow
   can be described as a success-signal + failure-signal + risk-trigger
   triple of the same structural shape Phase 1 validated. Read-only
   surfaces (blogs, dashboards) do not qualify; state-mutating flows
   with checkable post-conditions do.
3. **Known issue history usable for future auditor work (Phase 1.5).**
   Public issue tracker with at least a dozen closed regression-style
   bugs that could plausibly become auditor known-broken scenarios.
4. **License permits redistribution of seeded knowledge artifacts.**
   Permissive license (MIT, BSD, Apache 2.0, MPL 2.0) on the app, or
   copyleft only if knowledge artifacts in
   `experiments/regression_recall_real/knowledge/` can be distributed
   independently.

These criteria are sealed by this ADR. Adding a fifth criterion or
relaxing one of these four invalidates prior selection data per the
ADR-0009 precedent on sealing run-inputs.

### 2. Candidate evaluation against the sealed criteria

- **Conduit** (RealWorld reference Medium-clone, Node/Express + React,
  canonical Docker compose backend). C1 yes (under 10 minutes with
  pre-seeded demo data). C2 yes (login, register, publish-article,
  favorite, follow, comment all qualify; at least five flows). C3 yes
  (closed issues on the canonical reference repos, plus cross-
  implementation regressions in the RealWorld ecosystem). C4 yes (MIT).
- **Saleor** (production e-commerce, Django + GraphQL + Next.js
  storefront). C1 yes but tight (20-30 minutes, edge of ceiling). C2
  yes (browse, add-to-cart, checkout, account, admin; parallel to
  Phase 1). C3 yes (large public tracker). C4 yes (BSD-3-Clause).
- **OpenMRS** (open-source medical record system, Java + multiple
  frontend variants). C1 marginal (bring-up regularly exceeds 30
  minutes on first run; partial bring-ups leave empty patient data,
  forcing the porter to author fixtures before any flow runs). C2
  yes. C3 yes. C4 MPL 2.0, acceptable.

### 3. Recommendation: Conduit, with Saleor as fallback

Recommend Conduit. Cleanest fit on C1 (well under the ceiling), exceeds
the C2 floor, permissive license. Saleor is the fallback if Conduit
porting hits a blocker not visible from the criteria (for example, the
canonical backend turning out too thin to support multi-writer
contention scenarios). OpenMRS is rejected on C1: bring-up cost would
shift Phase 2 effort from the moat experiment to infrastructure
fighting.

Selection is not final until Pablo accepts this ADR. A Conduit-to-
Saleor switch during Phase 2 is recorded as a separate amending ADR,
not a silent re-pick.

### 4. Phase 2 goals: parallel-but-distinct from Phase 1

The Phase 2 port authors four-or-five Conduit goals, parallel in shape
to Phase 1's four so the cross-SUT recall delta is apples-to-apples.
Proposed slate: `login` (parallel to Phase 1 login), `search_articles`
(parallel to search), `publish_article` (parallel to checkout:
multi-step mutating flow with checkable post-condition),
`favorite_article` (a second mutating flow with idempotency questions
parallel to the checkout double-post risk), and
`admin_or_owner_access` (parallel to admin_access). Four-versus-five
is settled during porting; this ADR pre-registers the parallelism,
not the exact name list. Per ADR-0011, this ADR does not authorize
the Phase 2 multi-writer run, only the SUT pick and goal slate.

### 5. New run directory: `experiments/regression_recall_real/`

The port lands at `experiments/regression_recall_real/`, parallel and
independent of Phase 1's `experiments/regression_recall/`. The port
copies the Phase 1 harness shape (manifest, harness, metrics, runner,
judge prompt, budget, pre-registration, knowledge YAMLs) into the new
directory and authors fresh content for Conduit flows there. No sealed
Phase 1 artifact is edited.

### Forbidden alternatives

DO NOT do any of the following:

- DO NOT modify any sealed Phase 1 artifact under
  `experiments/regression_recall/`. Copies-then-edits land under
  `experiments/regression_recall_real/`. ADR-0010's provisional
  clearance depends on Phase 1 artifacts staying immutable.
- DO NOT piggyback new schema fields into this ADR or into the port's
  initial commits. Auth-state, session-state, or any other additive
  schema field surfaced by the port is decided in ADR-0017 and lands
  through the schema + pydantic model + agreement-test triple in the
  same future commit. Bundling SUT selection with PII-fragile schema
  is the canonical porting-leak shape and is rejected.
- DO NOT pick a candidate by reading its flow surface before sealing
  the criteria. Criteria are sealed by this ADR; the Conduit
  recommendation is justified against them, not the reverse.
- DO NOT extend the Phase 2 SUT slate beyond one app. Cross-app sweep
  is a Phase 3 question.
- DO NOT relax the C1 30-minute ceiling to admit a preferred candidate.
  A future desirable-but-slower candidate requires sealing a new
  criterion in a separate ADR, not widening this one silently.

## Consequences

Positive:

- The moat claim becomes testable on a public app a stranger to this
  repo can stand up. Phase 1's "operational knowledge about THIS app"
  stops being defensible only against an app the seed author wrote.
- The 30-minute bring-up ceiling protects Phase 2 effort from drifting
  into infrastructure work.
- Pre-sealing the criteria forecloses post-hoc rationalization; a
  future SUT switch is auditable against the same four criteria.
- Phase 1 sealed artifacts stay untouched; ADR-0010's provisional
  clearance is preserved.

Negative:

- Conduit is a Medium-clone, not a checkout-heavy e-commerce app. Some
  Phase 1 categories (coupon-stacking, `POST /orders` idempotency)
  have no exact Conduit analog; cross-SUT comparison is parallel in
  shape, not in content. If the moat is specific to e-commerce flow
  shapes, Saleor fallback exists for that case.
- A single Phase 2 SUT validates one additional point, not cross-app
  generalization. Cross-app sweep is Phase 3.
- Criteria sealed at acceptance cannot be quietly extended; a fifth
  criterion requires a new ADR.

Invariants respected (cited from the project invariant set):

- `knowledge-not-mbt-procedure-cache`: the Phase 2 goal slate is
  goal-shaped (success signals, failure signals, risks), not step-
  shaped; the port exercises operational knowledge on a different
  app, not a procedure cache.
- `operational-knowledge-not-procedures`: C2 requires state-mutating
  flows with checkable post-conditions, not read-only content
  surfaces; the port stays aligned with operational-knowledge claims.
- `no-procedures-secrets-or-run-data-as-storage-targets`: the new run
  directory mirrors Phase 1's event-log + projected knowledge shape,
  not a recorded-step archive.
- `runtime-agnostic-core`: SUT selection does not force a runtime
  pick. Conduit is reachable via HTTP probing the same way Phase 1
  reaches testapp.py. A future browser adapter (Stagehand head-to-
  head) is a separate Phase 1.5 decision.

Invariants this ADR explicitly does NOT cover:

- `no-secrets-tokens-pii-in-knowledge`: the Conduit port surfaces a
  session/auth-state question (logged-in flow has a token, cookie,
  user id). Redaction-and-shape decision is ADR-0017's scope.
- `schema-is-single-source-of-truth`: any schema extension surfaced by
  the port lands through ADR-0017, updating
  `schema/knowledge.schema.json` + pydantic model + agreement test
  together. This ADR does not touch the schema.

## Relation to prior ADRs

- Extends ADR-0009 (Phase 1 scope, regression-recall falsifier, praxis
  reframe): generalizes the same falsifier shape to a public app and
  pre-registers the SUT pick that makes the generalization auditable.
- Builds on ADR-0010 (Phase 1 verdict, Accepted): turns one of
  ADR-0010's named caveats (real-app generalization) into a Phase 2
  deliverable with sealed criteria.
- Coordinates with ADR-0011 (Phase 2 scope umbrella): ADR-0016 is the
  load-bearing real-app-SUT-port item ADR-0011 names; the Phase 1.5
  versus Phase 2 boundary (Stagehand head-to-head, auditor protocol,
  cross-model arm) is owned by ADR-0011.
- Forward-references ADR-0017 (additive auth-state schema extension):
  the schema half of P2-10 is split out per the brief's canonical-leak
  argument. ADR-0016 picks the app; ADR-0017 lands the additive field
  shape and redaction rules.
