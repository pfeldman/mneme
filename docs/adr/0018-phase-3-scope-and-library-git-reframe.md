# ADR-0018: Phase 3 scope and the library-plus-git reframe

Status: Accepted (2026-06-08)

## Context

ADR-0009 closed Phase 1 with the praxis reframe (operational knowledge,
not procedure cache) and ADR-0010 cleared the regression-recall gate
provisionally: memory beat `cold_readme` by a real margin on
`phase-1-r1`, so the moat survived its falsifier. ADR-0011 scoped Phase 2
to five load-bearing items (ADR-0012 through ADR-0017, all Accepted
2026-06-07) and, in its section 4, DEFERRED a block of trust-and-product
work to Phase 3: governance, RBAC, hosted multi-tenant shared memory,
dashboards, web UI, and pricing / GTM. Both `docs/07-roadmap.md` and the
Phase 3 paragraph in `AGENTS.md` still frame that block as a hosted SaaS
trust layer ("hosted shared memory", "the moat", monetization).

On 2026-06-08 Pablo rejected the SaaS framing ("no es un framework local,
subido a pypi y ya? realmente necesitamos un saas?"). The Phase 3 vision
is reframed to **library plus git, no SaaS**, with two halves:

- Knowledge is shared through git. A `git pull` brings the team the
  latest believed knowledge; a `git push` shares a discovery. There is no
  hosted backend.
- The brain that decides what to click and what to ask is pluggable. The
  local brain is Claude Code, delivered as skills with no API key, running
  on the user's own subscription. The CI brain is an API-key agent (the
  existing `live` extra), which the team invokes from its own CI; Praxis
  ships no reusable action.

A repo's knowledge lives under `.praxis/` (Pablo chose `.praxis/` over
`praxis/` on 2026-06-08: "less clutter, more serious"). Multi-tenancy is
one repo per project. Permissions are git permissions. Conflicts are git
merge conflicts. Auditability is git log.

This ADR is the Phase 3 mirror of ADR-0011 and ADR-0009: it names what
Phase 3 owns, replaces the hosted SaaS deferrals with git-native
equivalents, maps the on-disk layout onto the believed / contested
model, and locks the Phase 1.5 boundary so no later ADR in this batch
silently revives the hosted path. The seven owned items each get their
own ADR (0019 through 0025) later in this batch. Implementation is a
separate subsequent task once these ADRs are Accepted; this batch records
decisions, not code.

## Decision

### 1. The library-plus-git reframe and its two halves

Phase 3 turns Praxis from a phase-gated research repo into a shippable
library. The product is a pip-installable Python package plus a git
convention, not a hosted service. The two halves are:

1. **Git is the shared memory.** The committed knowledge under `.praxis/`
   IS the team-level store. Distribution of knowledge between teammates
   is `git pull` / `git push`; there is no server in the loop.
2. **The brain is pluggable, local first.** Praxis the library is the
   body (browser control, the knowledge store, the deterministic CLI).
   The brain is the LLM that reasons. Local development uses Claude Code
   via skills (no API key, on the user's subscription); CI uses an
   API-key agent. ADR-0019 owns this split in full.

### 2. The seven owned Phase 3 items, each with its owning ADR

Phase 3 ships exactly these seven, each with its own ADR later in this
batch:

1. **Brain pluggability and execution surfaces** (ADR-0019). The
   body-vs-brain split, the deterministic-vs-agentic operation classes,
   the two brains (local Claude Code skill, CI API-key agent), the dual
   surface, and the teach-is-skill-only exception. Foundational; the rest
   depend on it.
2. **PyPI packaging and distribution** (ADR-0020). Distribution name
   `praxis-qa` (import name and CLI command stay `praxis`), one pure-Python
   universal wheel, optional extras that keep the core runtime-agnostic
   (ADR-0003) and brain-agnostic (ADR-0019), schema shipped as package
   data, and the Claude Code skills scaffolded by `praxis init`.
3. **The `.praxis/` repository convention** (ADR-0021). The on-disk
   directory layout, the committed-vs-gitignored split, the reconciliation
   of the per-machine append-only event log (ADR-0001) with git history,
   and the one-file-per-candidate anti-conflict rule (ADR-0012 layout is
   load-bearing).
4. **The teach operation as a Claude Code skill** (ADR-0022). The
   human-in-the-loop teach skill (`/praxis:teach`), the interactive prompt
   protocol, the end condition, the teach-output-is-seeded-knowledge
   framing (the legitimate ADR-0005 seed path), and the
   credentials-never-persisted rule (ADR-0017).
5. **The regress and explore dual surface** (ADR-0023). A console CLI plus
   a Claude Code skill for both operations, the default-all aggregate
   report, and the OK / REGRESSED / STALE break-vs-drift verdict that is
   the core value over a plain test runner.
6. **CI integration by invoking the console commands** (ADR-0024). Praxis
   exposes the console commands the team wires into its own CI; Praxis owns
   no reusable action and no push / PR / auth (that is the team's git and
   CI), and promotion stays a human merge.
7. **The landing page and docs site** (ADR-0025). The minimal non-engineer
   evaluation story, explicitly with no analytics, no signup, no SaaS
   funnel.

### 3. The hosted SaaS deferrals are REPLACED by git-native equivalents

ADR-0011 section 4 deferred a hosted trust layer to Phase 3. The reframe
does NOT inherit those items as still-deferred-but-hosted. Each is
REPLACED by a git-native equivalent, recorded here so no later ADR
silently revives the SaaS path:

- **Hosted multi-tenant shared memory** becomes **one repo per project.**
  Each project owns its `.praxis/` tree; there is no shared backend and no
  cross-tenant store to isolate. The ADR-0011 / ADR-0012 tenant-scoping
  placeholder is satisfied by repo boundaries, not by hosted isolation.
- **Governance and RBAC** become **git permissions.** Who may read, write,
  or merge knowledge is whoever has the corresponding git access on the
  repo. Praxis adds no access-control layer of its own.
- **Dashboards and web UI** become **git log plus markdown reports.** The
  history of `.praxis/` is the audit trail; the regress and explore
  operations emit markdown reports (ADR-0023). There is no hosted
  dashboard.
- **Pricing / GTM** is **dropped.** There is no hosted service to price.
  The moat is the library plus the git convention, distributed for free.

Secret redaction beyond the adapter-boundary regex (the ADR-0011 P2-21
deferral) and poisoning detection beyond the diversity-or-seed gate (P2-22)
remain real Phase 3+ hardening concerns; the adapter-boundary redaction
rule (ADR-0009, ADR-0017) and the ADR-0008 source-independence gate stay
the binding rules and are NOT relaxed by this reframe.

### 4. `.praxis/knowledge` vs `.praxis/candidates` map onto believed vs contested

The on-disk layout is the believed / contested model made physical, and
it preserves ADR-0005 and ADR-0001 unchanged:

- `.praxis/knowledge/` holds **seeded / believed** knowledge: goals whose
  oracle is trusted under the ADR-0005 diversity-or-seed rule. This is the
  shared, committed, pulled-and-pushed team memory.
- `.praxis/candidates/` holds **contested** knowledge: agent-proposed
  risks and uncertainties (ADR-0014 `CandidateEvent` projection) that have
  not yet earned `believed`. Candidates are one-file-per-id so concurrent
  adds never merge-conflict (ADR-0012 file-per-event layout, load-bearing
  here).

**Promotion from candidate to believed is a human seed event, realized as
a git merge.** A human reviews a candidate and merges it into
`.praxis/knowledge/`; that merge IS the ADR-0005 first-oracle seed and the
ADR-0014 promotion-by-fresh-seed-event, expressed in git. The merge
appends to history; it never edits a candidate in place, so ADR-0001
append-only and the ADR-0005 "first oracle is seeded, not self-certified"
rule both hold. No counting of agents and no automatic promotion can move
a candidate into `.praxis/knowledge/`; only a human merge does. ADR-0021
owns the layout in full and ADR-0024 owns the CI promotion path; this ADR
fixes the believed-vs-contested mapping and the promotion semantics.

### Forbidden alternatives

DO NOT, in any Phase 3 ADR or implementation:

- Revive the hosted SaaS trust layer. The Phase 3 deferrals of ADR-0011
  section 4 are replaced by git-native equivalents, not deferred again as
  hosted work.
- Stand up a hosted multi-tenant backend or any shared knowledge server.
  Multi-tenancy is one repo per project; there is no cross-tenant store.
- Apply last-write-wins on shared knowledge. Concurrent writes are
  reconciled by the append-only store (ADR-0001) and git merge, never by
  one writer silently overwriting another's knowledge.
- Auto-promote a candidate into `.praxis/knowledge/` without a human merge.
  Promotion is a human seed event (ADR-0005); no count-based or automatic
  path exists.
- Absorb the Phase 1.5 items (Stagehand adapter and head-to-head, the paid
  API-key cross-model run, the auditor `refuted`-status protocol) into
  Phase 3. They stay deferred to Phase 1.5 per section 5 below.

### 5. Phase 1.5 items stay deferred and are NOT absorbed into Phase 3

The Phase 1.5 boundary that ADR-0011 section 3 locked is NOT moved by this
reframe. Phase 1.5 still owns and Phase 3 does NOT cover:

- The **Stagehand adapter and head-to-head regression-recall experiment**
  (deferred per ADR-0009 section 5 and ADR-0011 section 3). The
  moat-vs-procedural-cache result is Phase 1.5 work; the docs/06
  existential risk stays open and is not closed by shipping a library.
- The **paid cross-model API-key arm** (n=5-with-prompt-variation,
  independent `cold_readme` re-authoring, the ADR-0010 caveats). Phase 3's
  `live` extra carries an API-key brain for CI execution, which is NOT the
  same thing as the Phase 1.5 cross-model evaluation arm; running the
  product against an API key does not discharge the deferred experiment.
- The **auditor protocol with a `refuted` status enum value** (deferred
  per ADR-0009 section 6 and ADR-0011 section 3). The four-value status
  enum (`believed` / `contested` / `stale` / `quarantined`) stays through
  Phase 3; `refuted` remains Phase 1.5 work requiring diversity-or-seed
  over two independent failure detectors.

## Consequences

Positive:

- Phase 3 has a small, named, dependency-ordered work set of seven items,
  each with an owning ADR (0019 through 0025), mirroring the ADR-0011
  Phase 2 pattern.
- The no-SaaS reframe is recorded in one place. The hosted deferrals of
  ADR-0011 section 4 are explicitly replaced by git-native equivalents,
  so no later ADR has to re-decide them and none can silently revive the
  hosted path.
- The believed-vs-contested model is preserved through the product layer.
  `.praxis/knowledge/` is believed, `.praxis/candidates/` is contested,
  and promotion stays a human seed event via git merge, so ADR-0005 and
  ADR-0001 carry into Phase 3 unchanged.
- The Phase 1.5 boundary is reaffirmed once here; shipping a library does
  not let the deferred experiments be quietly counted as done.

Negative:

- Git as the shared memory adds a second history layer on top of the
  ADR-0001 per-machine event log. ADR-0021 owns the reconciliation (local
  event log stays the per-machine source of truth; git history of the
  committed projection is the shared append-only analog; force-push to
  `.praxis/` is the forbidden mutation). This ADR records the tension; it
  does not resolve it.
- Dropping the hosted layer drops the SaaS-shaped business model. The
  reframe is a deliberate bet that the library plus git convention is the
  product, accepted by Pablo on 2026-06-08.
- One repo per project means there is no built-in cross-project knowledge
  sharing. Teams that want shared knowledge across repos must arrange it
  with git (submodule, shared package, manual copy); Phase 3 does not
  provide it.

Invariants respected:

- `append-only-store-no-mutation`: knowledge is shared through git appends
  and merges; promotion appends a seed event and never edits a candidate
  in place. ADR-0001 carries into Phase 3.
- `first-oracle-must-be-seeded`: promotion from `.praxis/candidates/` to
  `.praxis/knowledge/` is a human seed event via git merge, the legitimate
  ADR-0005 seed path; no count-based or automatic promotion exists.
- `tenant-scoping-prevents-leakage`: one repo per project replaces the
  ADR-0011 / ADR-0012 hosted-multi-tenant placeholder; repo boundaries are
  the tenant boundaries.
- `no-secrets-tokens-pii-in-knowledge`: the adapter-boundary redaction
  rule (ADR-0009, ADR-0017) stays binding; nothing committed under
  `.praxis/` may carry secrets, tokens, or PII.
- `loud-and-traceable-over-silent-and-convenient`: the Phase 3-vs-Phase 1.5
  boundary and the replacement of hosted deferrals with git-native
  equivalents are named explicitly so a later contributor cannot silently
  revive the SaaS path or absorb the deferred experiments.

Invariants this ADR does NOT cover (deferred to the owning Phase 3 ADRs):

- `runtime-agnostic-core` extended to `brain-agnostic`: owned by ADR-0019.
- `schema-is-single-source-of-truth` under packaging (schema as package
  data): owned by ADR-0020.
- `concurrent-writes-lose-no-knowledge` under the git layout and the
  one-file-per-candidate rule: owned by ADR-0021.
- `provenance-and-confidence-mandatory`, `knowledge-not-mbt-procedure-cache`,
  and `invariants-not-coordinates-hierarchy` for the teach operation:
  owned by ADR-0022.
- `no-silent-success-when-app-broken` for the regress / explore report:
  owned by ADR-0023.
- `no-self-corroboration-source-independence` for the autonomous CI
  candidate path: owned by ADR-0024.

## Relation to prior ADRs

Mirror of ADR-0011 (Phase 2 scope umbrella, Accepted) and ADR-0009 (Phase
1 scope, Accepted): names what the phase owns, defers the rest, and locks
the phase boundary so later ADRs in this batch do not relitigate it.

Builds on ADR-0010 (Phase 1 verdict, Accepted): Phase 3 productizes Praxis
because the moat survived the regression-recall falsifier. The ADR-0010
caveats stay Phase 1.5 entry conditions, not Phase 3 blockers.

Supersedes the Phase 3 framing in `docs/07-roadmap.md` and the Phase 3
paragraph in `AGENTS.md` (hosted shared memory, trust-and-product layer,
monetization): the accept step of this batch propagates the
library-plus-git reframe into both documents.

Replaces the Phase 3 deferral list in ADR-0011 section 4
(governance / RBAC / hosted multi-tenant / dashboards / web UI / pricing)
with git-native equivalents per section 3 above, without superseding the
rest of ADR-0011. Does not supersede any other prior ADR; ADR-0001 through
ADR-0017 stay binding into Phase 3 and are re-cited by ADR-0019 through
ADR-0025 where they apply.
