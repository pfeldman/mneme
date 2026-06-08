# ADR-0024: CI integration by invoking the console commands; Praxis owns no CI machinery

Status: Accepted (2026-06-08)

## Context

ADR-0018 reframed Phase 3 as a library plus git, no SaaS, and named the CI
integration as the sixth of seven owned items. ADR-0019 fixed the two
brains: the local brain is Claude Code via skills (no API key, on the
user's subscription), and the CI brain is an API-key agent installed via
the existing `live` extra, used where there is no human subscription
session to borrow. ADR-0021 fixed where knowledge lives on disk
(`.praxis/knowledge/` committed and believed, `.praxis/candidates/`
committed and contested, one file per candidate observation, `runs/`
gitignored). ADR-0023 fixed the regress and explore operations: their
console CLI is the test-style red / green surface, the per-goal verdict is
OK / REGRESSED / STALE, and a REGRESSED verdict is a loud non-zero exit.

The original draft of this ADR proposed a reusable Praxis-owned GitHub
Action that opened draft candidate PRs and handled promotion wiring. Pablo
rejected that scope on 2026-06-08 as over-built for a library: "si quiero
meterlo en el ci, simplemente en github actions o donde sea llamo ese
comando y ya, guardo el resultado a un archivo del repo, problema del
equipo como pushear desde CI". The reframe's logic carries all the way
through: if permissions are git permissions and conflicts are git merge
conflicts, then CI is the team's own CI. Praxis exposes commands; the team
wires them into whatever continuous integration it already runs, and the
git mechanics (push, pull request, runner auth) are standard CI the team
owns, not a surface Praxis ships.

Two hazards still shape the design. First, CI is the obvious place to leak:
an API key lives in the runner environment, and a careless job could echo
it into a log or commit it, and the docs/06 shared-state-leakage failure
mode applies to the runner. Second, CI is the obvious place to short-circuit
trust: a job that auto-promotes its own exploration output into believed
knowledge would self-certify oracles and violate ADR-0005 (first oracle is
seeded, not self-certified) and ADR-0008 (source-independence). The CI brain
is one source and cannot promote itself.

## Decision

### 1. CI integration is invoking the console commands; Praxis ships no reusable action.

Praxis does NOT publish a reusable GitHub Action, a CI plugin, or any
turnkey CI product. CI integration is exactly: a team calls the Praxis
console commands (`praxis regress`, `praxis explore`, per ADR-0023) inside
whatever CI it already runs (GitHub Actions, GitLab CI, a cron box, a
Makefile). Praxis provides the commands and the loud non-zero exit a
REGRESSED verdict produces (ADR-0023); the team provides the workflow, the
runner, the secrets, and the git push. Where the original draft put a
Praxis-owned action between the command and the repo, this ADR removes it:
there is nothing between `praxis regress` and the team's CI but the team's
own workflow file.

### 2. Regress is gated by the loud non-zero exit, in any CI.

A team makes `praxis regress` (over every believed goal, ADR-0023) a
required check by running it in CI on pull requests and release tags and
failing the job on a non-zero exit. Because a REGRESSED verdict is a loud
process exit with the named goal and flipped signal (ADR-0023), ANY CI can
gate on it with no Praxis-specific integration: it is an exit code. A STALE
verdict is a knowledge-update task, not a code bug, so a team that wants to
distinguish it reads the report rather than hard-failing the gate; how a
team treats STALE is the team's CI policy, not a Praxis behavior. The gate
is R-mode, so it inherits the ADR-0009 leak closure: auditor scenarios are
NOT an input.

### 3. The CI brain is the API-key agent, supplied by the team.

When the console commands run autonomously in CI there is no human and no
subscription session, so they use the API-key agent from the `live` extra
(ADR-0019 section 3), never the Claude Code skills. The API key is the
team's CI secret, read from the runner environment by the `live` agent;
Praxis consumes it from the environment and never echoes it. Which brain ran
is execution provenance at most (`source_id = agent_identity`, ADR-0009 /
ADR-0014), never a stored field, so a green CI regress and a green local
regress mean the same thing about the knowledge. Any app login credential a
run needs is supplied the same way, as a runner secret or environment
variable (the ADR-0021 secrets channel), read at runtime and never written
into the repo or the logs.

### 4. Explore in CI writes contested candidate files; what happens next is the team's CI.

A team that wants autonomous exploration runs `praxis explore` in CI (on a
schedule, or on a labeled PR). The command writes any candidate risks and
uncertainties as ADR-0014 `CandidateEvent` projections, one file per
observation under `.praxis/candidates/<goal>/<id>.yaml` (the ADR-0021
layout, load-bearing so concurrent adds never merge-conflict), at status
`contested`. Praxis's responsibility ends there: it has written contested
candidate files to the working tree. Committing them, pushing them, and
opening a pull request for review are the team's standard CI and git, NOT
Praxis behaviors. Praxis never opens a PR, never pushes, and never
auto-merges; it writes files and exits.

### 5. Promotion stays a human git merge, however the team surfaces it.

However a team surfaces the candidate changes (a pull request its CI opens,
or a human committing the files), promotion from `.praxis/candidates/` to
`.praxis/knowledge/` is a human merging the candidate, which IS the ADR-0005
human seed event and the ADR-0014 promotion-by-fresh-seed-event, exactly as
ADR-0018 section 4 fixed. The merge appends to history and never edits a
candidate file in place (ADR-0001). The CI brain is one source
(`source_id = agent_identity`, ADR-0008) and cannot corroborate itself, so
no count of scheduled exploration runs and no agreeing-CI-jobs rule promotes
anything; only a human merge does. This is the same `praxis review`
promotion (ADR-0014) expressed in git, with no hosted review queue.

### 6. The teach operation never runs in CI.

The teach operation is skill-only and always human-in-the-loop (ADR-0019
section 5, ADR-0022). It never runs in CI, because CI has no human to answer
the interactive prompts and an autonomous CI teach would produce a
self-certified oracle, breaking ADR-0005. Seeding new goals stays a local
human teach session whose output a human commits and pushes.

### 7. Praxis ships a documented example workflow, not a product.

The only CI artifact Praxis ships is a documented, copy-paste example
workflow in the docs site (ADR-0025): a minimal GitHub Actions file that
runs `praxis regress` as a gate and, optionally, `praxis explore` on a
schedule. It is an EXAMPLE the team adapts, explicitly not a Praxis-owned,
versioned, or supported action. The team owns the push, the pull request,
the runner auth, and the secret wiring; the example shows one way to do it
and the team changes it freely. This keeps "permissions are git
permissions, conflicts are git merge conflicts, CI is the team's CI"
(ADR-0018) true all the way down.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Publish or maintain a reusable Praxis-owned CI action, plugin, or product
  that wraps git push, pull-request creation, or runner auth. Praxis exposes
  console commands; CI is the team's, and the only shipped artifact is a
  documented example (decision 7).
- Make Praxis responsible for pushing from CI, opening a PR, or
  authenticating the runner. "How to push from CI" is standard git the team
  owns (ADR-0018).
- Auto-merge candidate changes or auto-promote a candidate into
  `.praxis/knowledge/`. Promotion is a human merge, the ADR-0005 seed; no
  count of CI runs promotes (ADR-0008).
- Run the teach operation in CI. Teach is skill-only and human-in-the-loop
  (ADR-0019, ADR-0022); an autonomous CI teach would self-certify an oracle.
- Echo the API key or any token, cookie, user id, session id, or PII into
  the CI logs or the repository. The key is read from the runner secret and
  redaction at the boundary stays binding (ADR-0009, ADR-0017).
- Force-push to `.praxis/` from CI. Force-push is the forbidden mutation
  that rewrites the shared append-only history (ADR-0021); CI only appends
  commits.
- Feed auditor scenarios into the regress gate. R-mode excludes auditor
  scenarios as inputs; the ADR-0009 leak path stays closed in CI.

## Consequences

Positive:

- The CI story is as small as the reframe promised: a team runs a command
  and gates on its exit code. There is no Praxis CI product to install,
  version, or trust; any CI that can run a process and read an exit code can
  gate on `praxis regress`.
- Praxis stays a library. Not owning the push, the PR, or the runner auth
  keeps the maintenance surface and the trust surface small and keeps the
  "CI is the team's CI" posture honest.
- Promotion stays a human seed event expressed in git, exactly as in the
  local flow: a human merges the candidate, and the believed-vs-contested
  trust model carries into CI unchanged with no hosted review backend.
- The break-vs-drift verdict (ADR-0023) reaches CI for free: REGRESSED is a
  loud non-zero exit that blocks a merge, STALE is a report line the team's
  policy decides how to treat.

Negative:

- A team gets less turnkey CI than a published action would give. Wiring
  `praxis regress` into a workflow, supplying the secret, and arranging the
  candidate-PR flow is the team's work; the documented example (decision 7)
  is the only head start Praxis provides. This is the accepted cost of
  staying a library, not a CI product.
- Autonomous CI exploration consumes API-key budget on whatever cadence the
  team schedules; per-goal budget (ADR-0023) caps a single run, but the
  schedule frequency is an operating cost the team sets. A team that does not
  want this simply does not schedule `praxis explore` in CI; regress-gating
  alone needs no exploration.
- A backlog of unreviewed candidate changes can accumulate if the team opens
  PRs no one reviews, the same human-in-the-loop bottleneck ADR-0014 records
  for `praxis review`. Candidates decay to `stale` per ADR-0013 if
  uncorroborated, which caps the blast radius but does not remove the review
  burden.

Invariants respected:

- `append-only-store-no-mutation`: the console commands only write new
  contested candidate files; promotion is a human merge that appends and
  never edits a candidate in place, and CI is forbidden from force-pushing
  `.praxis/` (ADR-0021), so the committed history stays the append-only
  analog of the ADR-0001 event log.
- `first-oracle-must-be-seeded`: promotion from `.praxis/candidates/` to
  `.praxis/knowledge/` is a human merge, the legitimate ADR-0005 seed; the
  CI brain never self-promotes its exploration output.
- `no-self-corroboration-source-independence`: the CI brain is one source
  (`source_id = agent_identity`, ADR-0008), so N scheduled exploration runs
  of the same agent cannot promote their own candidates; the required
  independent seed is the human merge.
- `no-secrets-tokens-pii-in-knowledge`: the API key is read from the runner
  secret and never written into the repo or the logs; adapter-boundary
  redaction (ADR-0009, ADR-0017) binds the autonomous brain as it binds
  every other write path.
- `loud-and-traceable-over-silent-and-convenient`: regress gates on a loud
  non-zero exit with the named goal and flipped signal (ADR-0023); the
  no-auto-merge, no-auto-promote, no-force-push, and no-CI-teach rules are
  named explicitly so no later change can quietly route candidates past the
  human seed or grow a CI product behind the library.

Invariants this ADR does NOT cover:

- The minimal landing page and docs site, and the example workflow it
  hosts: owned by ADR-0025; this ADR fixes that the only CI artifact is a
  documented example and that the team owns the CI, ADR-0025 owns where the
  example lives.
- The OK / REGRESSED / STALE report contract and the per-goal budget rule:
  owned by ADR-0023; this ADR consumes the console surface and the exit-code
  contract, it does not re-derive them.
- The `.praxis/` directory layout, the committed-vs-gitignored split, and
  the one-file-per-observation anti-conflict rule: owned by ADR-0021; this
  ADR writes into that layout, it does not define it.
- The teach protocol that keeps a local seed legitimate: owned by ADR-0022;
  this ADR fixes only that teach never runs in CI.

## Relation to prior ADRs

Depends on ADR-0019 (brain pluggability and execution surfaces, Proposed)
for the CI brain: when the console commands run autonomously they use the
API-key `live` agent, never the local Claude Code skills, because CI has no
subscription session.

Depends on ADR-0023 (regress and explore dual surface and report, Proposed)
for the console surface and the loud non-zero exit a team gates on, and for
the contested candidate files `praxis explore` writes.

Depends on ADR-0021 (the `.praxis/` repository convention, Proposed) for the
one-file-per-observation candidate layout the explore command writes into
and for the no-force-push rule CI inherits.

Realizes the ADR-0014 (E-mode candidate persistence, Accepted) `praxis
review` promotion via git: a human merge of the candidate changes is the
promotion-by-fresh-seed-event, with no hosted review queue. It carries
ADR-0005 (oracle seed rule, Accepted) and ADR-0008 (source-independence,
Accepted) into CI: the CI brain is one source and cannot promote itself, so
the human merge is the required independent seed.

Builds on ADR-0018 (Phase 3 scope and the library-plus-git reframe,
Proposed): "CI is the team's CI" is the direct consequence of "permissions
are git permissions, conflicts are git merge conflicts". Points at ADR-0025
for the documented example workflow. Re-cites ADR-0001, ADR-0009, and
ADR-0017 where the autonomous runner touches them (append-only history, the
R-mode no-auditor-input leak closure, and adapter-boundary redaction); it
does not supersede any prior ADR.
