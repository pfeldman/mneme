# ADR-0024: GitHub Action praxis-action, the CI brain path

Status: Proposed

## Context

ADR-0018 reframed Phase 3 as a library plus git, no SaaS, and named the
GitHub Action CI brain as the sixth of seven owned items. ADR-0019 fixed
the two brains: the local brain is Claude Code via skills (no API key, on
the user's subscription), and the CI brain is an API-key agent installed
via the existing `live` extra, used where there is no human subscription
session to borrow. ADR-0021 fixed where knowledge lives on disk
(`.praxis/knowledge/` committed and believed, `.praxis/candidates/`
committed and contested, one file per candidate id, `runs/` gitignored).
ADR-0023 fixed the regress and explore operations: their console CLI is the
test-style red / green surface CI runs, and the per-goal verdict is
OK / REGRESSED / STALE. This ADR owns how those pieces compose into
continuous integration.

The reframe makes git the shared memory: a team shares discoveries by
committing under `.praxis/` and pulling each other's commits. CI is the
natural place to (a) gate every change against the believed knowledge so a
regression is caught before merge, and (b) run the paid exploration brain
on a schedule so the team keeps hunting off-happy-path without a human
babysitting a subscription session. Both jobs need the CI brain, because CI
has no human and no subscription session; the API-key agent (the `live`
extra) is the only surface available there, exactly as ADR-0019 section 3
records.

Two hazards shape the design. First, CI is the obvious place to leak: an
API key lives in the runner environment, and a careless job could echo it
into a log or commit it into the repo, and the docs/06 shared-state-leakage
failure mode applies to the runner exactly as it applies to the adapter
boundary. Second, CI is the obvious place to short-circuit trust: a job that
auto-promotes its own exploration output into believed knowledge would
self-certify oracles and violate ADR-0005 (first oracle is seeded, not
self-certified) and ADR-0008 (source-independence). The whole value of
running exploration in CI is lost if its output rides straight into
`.praxis/knowledge/` without a human; the CI brain is one source and cannot
promote itself.

## Decision

### 1. R-mode regress is a gate on every PR and release tag.

A reusable GitHub Action, `praxis-action`, runs `praxis regress` over every
goal in `.praxis/knowledge/` on every pull request and every release tag,
using the console surface (ADR-0023): test-style output, a process exit
code, no chat. The CI brain (the `live` API-key agent, ADR-0019 section 3)
drives the app. The job is a required status check: a REGRESSED verdict on
any goal is a LOUD failure (non-zero exit, the named goal, the signal that
flipped, per the ADR-0023 loud-failure contract) and blocks the merge. An
OK verdict passes the gate. A STALE verdict is surfaced as a distinct
non-blocking signal in the job output: the app changed on purpose and the
knowledge is outdated, which is a knowledge-update task, not a code bug, and
must not be silently swallowed nor treated as a hard gate failure. The gate
is R-mode only, so it inherits the ADR-0009 leak-path closure: auditor
scenarios are NOT an input to the regress gate.

### 2. The CI brain is the API-key agent, never the local skill.

The Action runs the API-key agent from the `live` extra (ADR-0019). It does
NOT run Claude Code skills, because skills are the local human-subscription
surface and there is no human or subscription session in CI. The API key is
supplied to the runner as a GitHub Actions secret and read from the
environment by the `live` agent; it is the only paid surface (ADR-0019
section 3) and exists solely because CI has no subscription session to
borrow. Which brain ran is execution provenance at most
(`source_id = agent_identity`, ADR-0009 / ADR-0014), never a stored field;
the gate result is identical whichever brain produces it, so a green CI
regress and a green local regress mean the same thing about the knowledge.

### 3. Scheduled / labeled E-mode opens a DRAFT PR of candidate files.

On a schedule (cron) or when a designated label is applied to a PR, the
Action runs `praxis explore` with the CI brain to hunt off-happy-path. Any
candidate risks and uncertainties the run produces are written as
ADR-0014 `CandidateEvent` projections into one file per id under
`.praxis/candidates/<goal>/<id>.yaml` (the ADR-0021 one-file-per-candidate
layout, load-bearing so concurrent adds never merge-conflict). The Action
then opens a DRAFT pull request adding those candidate files. The draft PR
is the delivery mechanism; it is never auto-merged. The exploration run's
raw event log stays under the gitignored `runs/` (ADR-0021) and is not
committed by the PR; only the projected candidate files are. The candidate
files enter at status `contested` per ADR-0014; nothing in this job sets a
candidate to `believed`.

### 4. Promotion is the human reviewing and merging that PR.

The git PR review of the draft candidate PR IS the `praxis review`
promotion step (ADR-0014) expressed in git, and the merge that moves a
candidate into `.praxis/knowledge/` IS the ADR-0005 human seed event,
exactly as ADR-0018 section 4 fixed for the believed-vs-contested mapping
and ADR-0014 fixed as promotion-by-fresh-seed-event. A human reviews the
draft PR, and merging it is the seed that promotes a candidate from
contested to believed. The merge appends to history and never edits a
candidate file in place, so ADR-0001 append-only holds; the CI brain is one
source (`source_id = agent_identity`, ADR-0008) and cannot corroborate
itself, so the human merge is the required independent seed. No count of
exploration runs, no number of agreeing CI jobs, and no automatic rule moves
a candidate into `.praxis/knowledge/`; only a human merge does. This
realizes the ADR-0014 `praxis review` promotion via git rather than a hosted
review queue.

### 5. The teach operation never runs in CI.

The teach operation is skill-only and always human-in-the-loop (ADR-0019
section 5, ADR-0022). The Action never runs teach, because CI has no human
to answer the interactive prompts and an autonomous CI teach would produce a
self-certified oracle, breaking ADR-0005. The Action's only agentic
operations are regress (the gate, decision 1) and explore (the scheduled /
labeled candidate path, decision 3). Seeding new goals stays a local human
teach session whose output a human commits and pushes.

### 6. No secrets in the repo or logs, and no force-push.

The Action never writes the API key, any token, cookie, user id, session
id, or other secret / PII into the repository or into the Action logs; the
adapter-boundary redaction rule (ADR-0009, ADR-0017) binds the runner as it
binds every other write path, and the API key is consumed only from the
runner environment via a GitHub Actions secret. The Action never force-
pushes to `.praxis/`: force-push is the forbidden mutation that would
rewrite the shared append-only history (ADR-0018 section "Negative",
ADR-0021), so the Action only appends commits (the draft candidate PR) and
merges (the human-driven promotion). The candidate PR job needs only
contents and pull-request write scope, never history-rewrite permission.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Auto-merge a draft candidate PR. The draft PR is opened by CI and merged
  only by a human; auto-merge would skip the ADR-0005 seed event.
- Promote a candidate into `.praxis/knowledge/` without a human merge. No
  count of exploration runs and no agreeing-CI-jobs rule promotes; only a
  human merge is the seed event (ADR-0005, ADR-0014).
- Write the API key, any token, cookie, user id, session id, or PII into the
  Action logs or the repository. The key is read from the runner secret and
  redaction at the boundary stays binding (ADR-0009, ADR-0017).
- Force-push to `.praxis/` or otherwise rewrite the committed history. The
  Action only appends commits and opens PRs; force-push is the forbidden
  mutation (ADR-0021).
- Run the teach operation in CI. Teach is skill-only and human-in-the-loop
  (ADR-0019, ADR-0022); an autonomous CI teach would self-certify an oracle.
- Feed auditor scenarios into the regress gate. R-mode excludes auditor
  scenarios as inputs; the ADR-0009 leak path stays closed in CI.

## Consequences

Positive:

- Every PR and release tag is gated against the believed knowledge, so a
  regression that flips a believed signal is caught before merge with a loud
  named failure (ADR-0023), not discovered later in production.
- The break-vs-drift distinction reaches CI: REGRESSED blocks the merge as a
  real bug, while STALE is surfaced as a non-blocking knowledge-update
  signal, so an intentional app change does not masquerade as a regression
  and a real regression is never silently passed.
- The team keeps hunting off-happy-path without a human babysitting a
  subscription session: scheduled exploration runs on the paid CI brain and
  delivers its findings as a reviewable draft PR.
- Promotion stays a human seed event expressed in git. The git PR review is
  the `praxis review` step (ADR-0014) and the merge is the ADR-0005 seed, so
  the believed-vs-contested trust model carries into CI unchanged and no
  hosted review backend is needed.

Negative:

- A flaky CI brain or a flaky app can produce a false REGRESSED that blocks a
  merge. The loud-failure contract makes the flip traceable (named goal,
  flipped signal) so a human can triage it as flake vs real, but CI flake
  becomes a cost the gate carries; the STALE verdict path absorbs intentional
  drift but not nondeterminism.
- The scheduled exploration brain is the paid surface, so CI exploration
  consumes API-key budget on a cadence; per-goal budget allocation (ADR-0023)
  caps a single run, but the schedule frequency is an operating cost the team
  sets.
- A backlog of draft candidate PRs can accumulate if no human reviews them,
  the same human-in-the-loop bottleneck ADR-0014 records for `praxis review`.
  Candidates decay to `stale` per ADR-0013 if uncorroborated, which caps the
  blast radius but does not remove the review burden.

Invariants respected:

- `append-only-store-no-mutation`: the Action only appends commits (the
  draft candidate PR) and merges (the human promotion); it never edits a
  candidate file in place and never force-pushes, so the committed history
  stays the append-only analog of the ADR-0001 event log (ADR-0021).
- `first-oracle-must-be-seeded`: promotion from `.praxis/candidates/` to
  `.praxis/knowledge/` is the human merge of a draft PR, the legitimate
  ADR-0005 seed; the CI brain never self-promotes its exploration output.
- `no-self-corroboration-source-independence`: the CI brain is one source
  (`source_id = agent_identity`, ADR-0008), so N scheduled exploration runs
  of the same agent cannot promote their own candidates; the required
  independent seed is the human merge.
- `no-secrets-tokens-pii-in-knowledge`: the API key is read from the runner
  secret and never written into the repo or the logs; adapter-boundary
  redaction (ADR-0009, ADR-0017) binds the runner.
- `loud-and-traceable-over-silent-and-convenient`: a REGRESSED verdict is a
  non-zero exit with the named goal and flipped signal that blocks the merge
  (ADR-0023); a STALE verdict is surfaced rather than swallowed; the
  no-auto-merge, no-auto-promote, and no-force-push rules are named
  explicitly so no later change can quietly route candidates past the human
  seed.

Invariants this ADR does NOT cover:

- The minimal landing page and docs site, including its no-analytics /
  no-signup posture, is owned by ADR-0025; this ADR covers only the CI brain
  path.
- The OK / REGRESSED / STALE report contract and the per-goal budget rule are
  owned by ADR-0023; this ADR consumes them and wires them into CI, it does
  not re-derive them.
- The `.praxis/` directory layout, the committed-vs-gitignored split, and the
  one-file-per-candidate anti-conflict rule are owned by ADR-0021; this ADR
  writes into that layout, it does not define it.
- The teach protocol that keeps a local seed legitimate is owned by ADR-0022;
  this ADR fixes only that teach never runs in CI.

## Relation to prior ADRs

Depends on ADR-0019 (brain pluggability and execution surfaces, Proposed)
for the CI brain: the Action runs the API-key `live` agent, never the local
Claude Code skills, because CI has no subscription session.

Depends on ADR-0021 (the `.praxis/` repository convention, Proposed) for
where candidate files land and for the no-force-push rule: the Action writes
one file per candidate id under `.praxis/candidates/` and only appends and
merges, never rewrites the committed history.

Depends on ADR-0023 (regress and explore dual surface and report, Proposed)
for the console surface the gate runs and for the OK / REGRESSED / STALE
verdict the gate enforces; this ADR wires that console surface into CI as a
required status check.

Realizes the ADR-0014 (E-mode candidate persistence, Accepted) `praxis
review` promotion via git: the git PR review of a draft candidate PR is the
review step, and the human merge into `.praxis/knowledge/` is the
promotion-by-fresh-seed-event. It carries ADR-0005 (oracle seed rule,
Accepted) and ADR-0008 (source-independence, Accepted) into CI: the CI brain
is one source and cannot promote itself, so the human merge is the required
independent seed.

Builds on ADR-0018 (Phase 3 scope and the library-plus-git reframe,
Proposed) section 4, which fixed the believed-vs-contested mapping and that
promotion is a human seed event via git merge; this ADR is the CI
realization of that promotion path. Re-cites ADR-0001, ADR-0009, and
ADR-0017 where the runner touches them (append-only history, the R-mode
no-auditor-input leak closure, and adapter-boundary redaction); it does not
supersede any prior ADR.
