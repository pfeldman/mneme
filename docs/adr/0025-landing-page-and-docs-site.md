# ADR-0025: Landing page and docs site

Status: Proposed

## Context

ADR-0018 reframed Phase 3 as a library plus git, no SaaS, and named seven
owned items. This ADR owns the last of them: how Praxis is presented to a
person evaluating whether to adopt it. The other six Phase 3 ADRs decide
the product (ADR-0019 the brain split, ADR-0020 packaging, ADR-0021 the
`.praxis/` convention, ADR-0022 teach, ADR-0023 the regress / explore dual
surface, ADR-0024 the CI action). None of them decides the public-facing
story, and the no-SaaS reframe makes that story a deliberate decision
rather than a default: a hosted product would have come with a marketing
funnel; a library distributed for free does not, and the landing page has
to reinforce that posture rather than quietly contradict it.

The evaluation story is for a non-engineer. The Phase 3 ergonomic goal
across ADR-0022 and ADR-0023 is a QA engineer or product owner who does
not script test runners: the teach operation is human-in-the-loop in
natural language, and the regress report distinguishes a real bug
(REGRESSED) from intentional drift (STALE) in plain words. The landing
page serves the same reader. It explains what Praxis is, why operational
knowledge survives where procedure caches rot (the ADR-0009 thesis), and
how a team shares that knowledge through git, without assuming the reader
writes code.

There is a real risk in a marketing surface for a project whose entire
premise is that bad knowledge is silent (docs/06). A landing page that
inflates the evidence is exactly the loud-vs-silent failure the project
exists to avoid, turned on the project itself. ADR-0010 cleared the Phase 1
regression-recall gate provisionally, and that single result is the only
validated quantitative claim Praxis has. The Phase 1.5 experiments that
would harden it (the Stagehand head-to-head, the paid cross-model arm)
stay deferred per ADR-0018 section 5, so the docs/06 existential
moat-vs-procedural-cache question is still open. The site must not paper
over that.

The docs already live under `docs/` as markdown, so the lowest-friction
publishing path is a static-site generator that renders that tree in
place, not a separately authored marketing site that drifts from the ADRs.

## Decision

### 1. The audience is a non-engineer evaluating Praxis.

The landing page and docs site target a QA engineer, product owner, or
team lead deciding whether to adopt Praxis, NOT a contributor reading the
internals. The page answers, in plain language: what operational knowledge
is and why it outlives a procedure cache (the ADR-0009 reframe), what a
team gets from sharing it through git (ADR-0021), and how teach / regress /
explore feel to use (ADR-0022, ADR-0023). The reader is assumed not to
script test runners; the copy explains the break-vs-drift verdict
(REGRESSED vs STALE, ADR-0023) in product terms, not in exit codes.

### 2. The example set is testapp, Conduit, and one real OSS app.

The site demonstrates Praxis on a fixed, honest example set:

- **testapp** (the Phase 0 / Phase 1 toy SUT): the minimal, fully
  controlled walkthrough that shows the teach-then-regress loop end to end.
- **Conduit** (the ADR-0016 recommended real-app SUT): the realistic
  example, a non-toy SPA, demonstrating that the knowledge layer is not
  tied to `testapp.py`.
- **One real OSS application**: a third, externally recognizable app so the
  reader sees Praxis against something they did not build. The specific
  pick is an implementation choice for the subsequent task; this ADR fixes
  that there is exactly one such example and that it is a real, public app.

The example set is the demonstration; it is not a benchmark and the site
does not present it as one.

### 3. No analytics, no signup, no SaaS funnel.

The site has NO analytics, NO signup flow, NO email capture, NO hosted
account, and NO SaaS funnel of any kind. There is nothing to sign up for
because there is no hosted service (ADR-0018 dropped pricing / GTM). The
calls to action are `pip install praxis-qa` (ADR-0020) and the repository
link; the conversion event is a `git clone`, not a lead. This is a direct
reinforcement of the no-SaaS reframe: a signup or an analytics beacon would
imply a backend the reframe explicitly removed.

### 4. The static-site tooling is mkdocs-material, published from `docs/`.

The recommended tooling is mkdocs-material, published to GitHub Pages from
the existing `docs/` tree. This is the lowest-friction pick because the
docs are already markdown under `docs/`; mkdocs renders that tree in place,
so the published site stays sourced from the same files the ADRs live
beside and cannot drift into a separately maintained marketing site.
Publishing is GitHub Pages from the repository, consistent with the
git-native, no-hosted-backend posture of ADR-0018. The specific mkdocs
configuration and theme details are an implementation choice for the
subsequent task; this ADR fixes the generator, the source tree, and the
publishing target.

### 5. The only quantitative claim allowed is the ADR-0010 Phase 1 number.

The site makes NO marketing claim beyond the single validated result. The
only quantitative claim permitted is the ADR-0010 Phase 1 regression-recall
gate outcome (memory beats `cold_readme` on `phase-1-r1`), and it is
presented with its provisional status intact: it is one experiment on one
SUT at small n, not a general benchmark. No invented percentages, no
unrun comparisons, no "10x" framing, no implied head-to-head against a
procedural cache (the Stagehand result is Phase 1.5 and deferred per
ADR-0018 section 5). Any claim the site makes is traceable to an ADR or it
does not appear. The thesis itself (operational knowledge outlives a
procedure cache) is presented as the project's bet, sourced to ADR-0009,
not as a proven fact.

### Forbidden alternatives

DO NOT, in the landing page, the docs site, or its implementation:

- Add analytics, telemetry, or any tracking beacon. There is no backend to
  receive it and no consent surface to justify it.
- Add a signup flow, an email-capture form, a waitlist, or a hosted
  account. There is no hosted service (ADR-0018 dropped pricing / GTM);
  the call to action is `pip install` plus the repo.
- Stand up a SaaS funnel or any conversion path that implies a hosted
  product. A marketing funnel contradicts the no-SaaS reframe of ADR-0018.
- Make any marketing claim beyond the validated Phase 1 number. No invented
  metrics, no unrun benchmarks, no "Nx faster", no implied
  moat-vs-procedural-cache result while the Stagehand head-to-head stays
  deferred to Phase 1.5.
- Author a separate marketing site that drifts from `docs/`. The site is
  generated from the `docs/` tree so the published copy and the ADRs stay
  one source.

## Consequences

Positive:

- The public surface reinforces the reframe instead of fighting it. A
  reader who lands on the site sees `pip install` and a git clone, not a
  signup, which matches the library-plus-git product ADR-0018 chose.
- The honest-claims rule keeps the project's own marketing from becoming
  the silent-bad-knowledge failure it warns against. Every claim on the
  site is traceable to an ADR or it is not made.
- Generating the site from `docs/` with mkdocs-material means the site
  costs almost nothing to maintain and cannot drift from the decision
  record, because it renders the same files.
- The fixed example set (testapp, Conduit, one OSS app) gives a
  non-engineer a concrete, honest demonstration without overclaiming a
  benchmark.

Negative:

- A landing page with no funnel and one cautious quantitative claim
  converts worse than an aggressive marketing page would. That is the
  accepted cost of the no-SaaS, honest-claims posture; the reframe is a bet
  that the library plus git convention earns adoption on substance.
- Tying the site to the `docs/` tree means the public copy is only as
  approachable as the internal docs. Making `docs/` legible to a
  non-engineer is now part of the docs-site work, an implementation cost
  the subsequent task carries.
- The one-real-OSS-app example must be kept working as that app evolves, a
  small ongoing maintenance burden the implementation task owns; if the
  pick rots, the example must be refreshed rather than left stale.

Invariants respected:

- `loud-and-traceable-over-silent-and-convenient`: applied to the
  project's own claims. The only quantitative claim is the traceable
  ADR-0010 number presented with its provisional status; an inflated
  landing page would be the silent-bad-knowledge failure (docs/06) turned
  on Praxis itself, and the honest-claims rule forbids it.
- `operational-knowledge-not-procedures`: the pitch sells the ADR-0009
  thesis - operational knowledge (what success means, what is risky, what
  is unknown) outlives a disposable procedure cache - not a recorder or a
  selector engine. The site frames Praxis as the knowledge layer, never as
  a test-step recorder.

Invariants this ADR does NOT cover:

- None new. This ADR is a presentation-layer decision; it activates no
  schema field, defines no store or projection behavior, and reads or
  writes no knowledge. The packaging surface it points the reader at
  (`pip install praxis-qa`) is owned by ADR-0020; the git-sharing story it
  describes is owned by ADR-0021; the operations it demonstrates are owned
  by ADR-0022, ADR-0023, and ADR-0024. The site only reflects those
  decisions; it does not make them.

## Relation to prior ADRs

Depends on ADR-0018 (Phase 3 scope and the library-plus-git reframe,
Proposed) for the no-SaaS posture this site reinforces: the
no-analytics / no-signup / no-funnel rule is the public-facing expression
of ADR-0018's dropped pricing / GTM and replaced hosted deferrals.

Cites ADR-0010 (Phase 1 regression-recall gate cleared provisionally,
Accepted) as the single source of the only quantitative claim the site is
allowed to make, presented with its provisional status intact.

Presents the ADR-0009 thesis (operational knowledge, not procedure cache,
Accepted) as the project's bet, and points the reader at the surfaces owned
by ADR-0020 (`pip install praxis-qa`), ADR-0021 (git as shared memory),
ADR-0022 (teach), ADR-0023 (regress / explore), and ADR-0024 (the CI
action) without re-deciding any of them. The example set names Conduit per
ADR-0016 (real-app SUT selection, Accepted). Does not supersede any prior
ADR.
