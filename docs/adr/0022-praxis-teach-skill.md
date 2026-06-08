# ADR-0022: The teach operation as a Claude Code skill

Status: Proposed

## Context

ADR-0018 reframed Phase 3 as a library plus git with no SaaS, and named
the teach operation as one of its seven owned items. ADR-0019 classified
the operations into deterministic and agentic, fixed the dual surface for
regress and explore, and fixed the single rule that teach is the
exception: skill-only, never a bare CLI command, because it is always
human-in-the-loop. This ADR owns the teach protocol in full.

The teach operation is new behavior without precedent in Phase 0 through
Phase 2. Phase 0 through Phase 2 had no interactive author-facing
operation at all: knowledge was seeded by hand into example files, or
emitted by R-mode and E-mode runners that read a believed projection and
reported, and the only human-in-the-loop surface was `praxis review`
(ADR-0014), which triages a contested queue but never drives a browser
and never asks a question mid-run. The teach operation is the first
operation that drives a live app while blocking on a human answer. The
interactive prompt-and-reply loop, the end condition, and the
authoring-from-zero path all have no prior art in this repo.

The use case teach serves is a non-engineer or a QA engineer who has a
goal in natural language ("a user can log in", "an admin can delete a
draft article") and no knowledge file yet. The teach operation turns that
intent into a seeded goal YAML by exploring the live app until it can
perform the happy path, asking the human when it is blocked, and emitting
a file the human reviews before commit. Because the human confirms the
result, the teach output is a legitimate human seed under ADR-0005, not a
self-certified oracle. ADR-0019 fixed why this must be a skill and not a
command: an autonomous CLI teach would have no human to answer the
interactive prompts and would produce self-certified oracles, breaking
the ADR-0005 first-oracle-must-be-seeded rule. This ADR fixes the
protocol that keeps the seed legitimate.

The credential handling makes teach the highest-leakage operation in the
product. A teach session must drive a real login, so the human types real
credentials, and the trace at write time contains tokens, cookies, and
session identifiers. ADR-0017 already named this as the canonical leak
shape under authoring pressure and fixed the rule for the auth surface:
record the abstract posture (`authenticated` plus `scope`), never the
secret that produced it. Teach inherits that rule and tightens it for the
interactive case.

## Decision

### 1. Teach is delivered as a Claude Code skill, not a bare CLI command.

The teach operation ships only as the Claude Code skill `/praxis:teach`,
scaffolded into the project's `.claude/skills/` by `praxis init`
(mechanism owned by ADR-0020). There is no `praxis teach` console
command and no CI teach path, per ADR-0019 section 5. The skill runs the
local Claude brain on the user's subscription with no API key (ADR-0019
section 3); the brain reasons about what to click and what to ask, and
drives the live app through the ADR-0003 Playwright adapter, reading and
writing knowledge only through the two-method SPI (`read_knowledge`,
`write_observations`). The teach operation is always human-in-the-loop:
the agent asks the human when blocked and the human confirms before
anything is written.

### 2. Interactive prompt protocol with typed questions.

When the agent is blocked, it asks the human a question of exactly one of
four declared types, never an open-ended free-text dump:

- **credential**: the agent needs a secret to proceed past an auth wall
  (a username and password, a one-time code). The reply drives the
  browser for this session only and is governed by decision 5.
- **navigation-hint**: the agent cannot find the affordance that advances
  the happy path and asks where it is in app terms ("which control opens
  the editor", "is there a confirmation step"). The reply is a behavioral
  or text hint, never a CSS selector or coordinate.
- **role**: the agent needs the abstract scope the goal targets
  (`anonymous`, `user`, `admin`, or a SUT-specific role string) so the
  emitted `auth_state.scope` is correct under ADR-0017.
- **confirmation**: the agent believes it observed the happy path and
  asks the human to confirm that what it reached is the intended success
  state. This is the human seed act of decision 4.

Each prompt names its type so the protocol is machine-checkable and the
credential type can be routed to the never-persist path of decision 5.
The agent prefers behavioral, network, accessibility, text, and url
hints in that order (the ADR five-non-negotiables hierarchy); a
navigation-hint reply that names a raw selector or coordinate is
recorded as the behavior it points at, never as the selector itself.

### 3. End condition: happy path observed AND human confirm, with a backstop.

A teach session ends successfully only when BOTH hold: the agent observed
the happy path (a success signal of believed-grade evidence under
ADR-0005, ideally behavioral plus network diversity), AND the human
answered a confirmation prompt affirming that the reached state is the
intended success. Neither half alone ends the session: an observed-but-
unconfirmed path stays open, and a confirmation without an observed path
is rejected because there is no signal to seed.

A budget plus a wall-time backstop bounds a session that never converges.
The session carries a per-session action budget and a wall-clock limit;
when either is exhausted before the dual end condition is met, the
session terminates LOUDLY as incomplete. An incomplete session writes no
goal to `.praxis/knowledge/` and emits a traceable not-converged event
naming what was reached and what was missing, so the failure is visible
and re-runnable rather than a silent empty file.

### 4. Teach output is human-seeded knowledge under ADR-0005.

The artifact of a successful teach session is a goal YAML whose success
oracle is a SEEDED oracle: its provenance carries `source_type` of
`human` (the confirming human), the legitimate first-oracle seed path of
ADR-0005. Teach is precisely the human-or-spec seed branch of the
ADR-0005 diversity-or-seed rule; it does not self-certify by agent count.
Provenance and confidence are mandatory on every emitted signal and risk,
and author plus timestamp on every uncertainty (ADR-0004), exactly as the
seeded example files carry them. The human reviews the emitted YAML
before commit; the commit into `.praxis/knowledge/` is where the seed
lands (the layout and the commit semantics are owned by ADR-0021). The
emitted knowledge is operational, not procedural: success and failure
signals, risks with structured triggers, and uncertainties, never a
click-by-click recording of the path the agent took to reach them.

### 5. Credentials typed during teach drive the browser but are NEVER persisted.

A credential reply (decision 2) is used to drive the live browser for the
current session and is then discarded. It is never written to any file
under `.praxis/`, never logged, never echoed into an emitted signal,
risk, uncertainty, or run record, and never committed. What the session
records about authentication is the ADR-0017 abstract posture only:
`auth_state.authenticated` derived from observable behavioral and network
signals, and `auth_state.scope` as the abstract role, with the
adapter-boundary validator rejecting tokens, cookies, user IDs, session
IDs, JWT contents, and PII exactly as ADR-0017 section 2 specifies. The
credential the human types is the input to the browser, not an output of
the knowledge; the secret crosses no persistence boundary.

### 6. A teach session refuses to silently overwrite a believed goal.

If the named goal already exists in `.praxis/knowledge/` with a believed
oracle, the teach session does NOT overwrite it in place. It detects the
existing believed goal, declines to mutate it, and instead emits a
candidate refinement under `.praxis/candidates/` (an ADR-0014
`CandidateEvent`, contested by default) that proposes the change. The
existing believed knowledge is preserved; promoting the refinement
requires the same human-seed-via-git-merge promotion ADR-0018 section 4
fixed, never an in-place edit. This keeps the append-only contract
(ADR-0001) and stops a re-teach from quietly replacing a trusted oracle
with a single fresh session's view.

### Forbidden alternatives

DO NOT, in any teach implementation:

- Persist credentials, cookies, bearer tokens, session identifiers, or
  PII typed during a teach session, in any file under `.praxis/`, any
  log, any run record, or any emitted signal. The browser consumes the
  secret; knowledge records only the ADR-0017 abstract posture.
- Write teach steps as a click-by-click procedure or a selector
  recording. The output is operational knowledge (signals, risks,
  uncertainties), not the path taken; persisting the path is the failure
  mode this project exists to avoid.
- Auto-promote a teach result past the human confirm. The confirmation
  prompt (decision 2, decision 3) is the seed act; an observed path
  without a human confirm never lands in `.praxis/knowledge/`.
- Overwrite a believed goal in place. A re-teach of a believed goal emits
  a contested candidate refinement; promotion is a human seed via git
  merge (ADR-0018, ADR-0021), never an in-place mutation.
- Ship the teach operation as an autonomous console `praxis teach` command
  or run it in CI. Teach is skill-only and human-in-the-loop per ADR-0019
  section 5; an autonomous teach produces self-certified oracles.

## Consequences

Positive:

- A non-engineer can author a first goal from a natural-language intent
  without writing YAML by hand, and the result is a legitimate ADR-0005
  human seed because the human confirms the success state. The
  cold-start authoring path is interactive without weakening the oracle
  rule.
- The highest-leakage operation in the product has the tightest secret
  contract. Credentials drive the browser and are discarded; knowledge
  records only the ADR-0017 abstract posture, so a teach session cannot
  bake a token into shared, committed, pulled-and-pushed knowledge.
- A re-teach of a believed goal cannot quietly replace a trusted oracle.
  The no-silent-overwrite rule routes the refinement into the contested
  queue, preserving the believed knowledge until a human promotes the
  change via git merge.
- The end condition is loud on both halves. An unconverged session writes
  no goal and emits a traceable not-converged event, so a half-taught
  goal never masquerades as a believed one.

Negative:

- Teach is skill-only, so there is no scriptable or CI authoring path. A
  team that wants to bulk-seed goals must run interactive sessions one at
  a time; that is intentional (the human confirm is the seed) but it caps
  authoring throughput.
- The interactive prompt-and-reply loop driving a live browser while
  blocking on a human answer is genuinely new and carries the
  implementation risk ADR-0018 and the task plan record. This ADR fixes
  the protocol; the driving mechanics land in the implementation task.
- The no-silent-overwrite rule means a believed goal that genuinely went
  stale still requires a human merge to update, even when the re-teach is
  obviously correct. The friction is intentional (it is the ADR-0005
  seed friction) but it slows correcting a stale believed oracle.

Invariants respected:

- `first-oracle-must-be-seeded`: the teach output is a human-confirmed
  seed (`source_type` human), the legitimate ADR-0005 first-oracle path;
  teach never self-certifies by agent count, and an unconfirmed path
  never becomes believed.
- `provenance-and-confidence-mandatory`: every emitted signal and risk
  carries provenance plus confidence, and every uncertainty carries
  author plus timestamp (ADR-0004); the write path rejects entries that
  lack them.
- `no-secrets-tokens-pii-in-knowledge`: credentials typed during teach
  drive the browser and are discarded; only the ADR-0017 abstract
  `auth_state` posture is recorded, with the adapter-boundary validator
  rejecting tokens, cookies, IDs, and PII.
- `knowledge-not-mbt-procedure-cache`: the teach artifact is operational
  knowledge (signals, risks, uncertainties), never a click-by-click
  recording of the path the agent took.
- `invariants-not-coordinates-hierarchy`: navigation-hint replies are
  recorded as the behavior, network, accessibility, text, or url
  invariant they point at, in that preference order, never as a raw
  selector or coordinate.
- `append-only-store-no-mutation`: a re-teach of a believed goal appends
  a contested candidate; it never edits the believed goal in place, and
  promotion is a human seed event (ADR-0001, ADR-0014, ADR-0018).
- `loud-and-traceable-over-silent-and-convenient`: an unconverged session
  writes no goal and emits a traceable not-converged event; the typed
  prompt protocol and the no-silent-overwrite rule keep every authoring
  decision visible.

Invariants this ADR does NOT cover:

- `no-silent-success-when-app-broken` and the OK / REGRESSED / STALE
  report contract: owned by ADR-0023. Teach authors a goal; the regress
  aggregation and the break-vs-drift verdict over believed goals are the
  regress operation's job, not teach's.
- `no-self-corroboration-source-independence` for the CI candidate path:
  owned by ADR-0024. Teach never runs in CI; the CI brain's
  candidate-PR writes and their source-independence are ADR-0024's job.
- `concurrent-writes-lose-no-knowledge` and the one-file-per-candidate
  layout for the candidate refinement teach emits: owned by ADR-0021
  (file-per-event layout) and ADR-0012 (multi-writer contract); this ADR
  routes the refinement into a candidate but does not re-derive the
  concurrency guarantees.
- `schema-is-single-source-of-truth` under packaging and skill
  distribution: owned by ADR-0020. This ADR fixes the teach protocol;
  ADR-0020 fixes how the `/praxis:teach` skill ships in the wheel and is
  scaffolded by `praxis init`.

## Relation to prior ADRs

Depends on ADR-0019 (brain pluggability and execution surfaces, Proposed)
for the skill-only, human-in-the-loop teach surface: ADR-0019 fixed that
teach is the skill-only exception and why; this ADR owns the protocol that
makes its output a legitimate seed.

Depends on ADR-0021 (the `.praxis/` repository convention, Proposed) for
where the teach output lands: a confirmed goal commits into
`.praxis/knowledge/` and a refinement of a believed goal lands as a
candidate under `.praxis/candidates/`, with the commit and layout
semantics owned there.

Depends on ADR-0017 (auth_state additive field, Accepted) for the
credentials-never-persisted rule: teach records only the abstract
`auth_state` posture and inherits the adapter-boundary validator that
rejects tokens, cookies, IDs, and PII.

Depends on ADR-0005 (oracle trust by diversity, seeded cold-start,
Accepted) for the seed rule: the human confirmation in a teach session is
the human-or-spec seed branch of the diversity-or-seed rule, so the teach
output is a legitimate first oracle and not a self-certified one. Inherits
ADR-0004 (provenance plus confidence mandatory) on every emitted
assertion.

Depends on ADR-0003 (runtime adapter boundary, Accepted) for the live-app
access: the teach brain drives the app through the Playwright adapter and
the two-method SPI, never bypassing the adapter boundary, so the
adapter-boundary redaction of decision 5 stays the runtime defense.

Re-cites ADR-0001 (append-only store) and ADR-0014 (CandidateEvent) for
the no-silent-overwrite path: a re-teach of a believed goal appends a
contested candidate rather than mutating, preserving immutability.
Re-cites ADR-0018 (the library-plus-git reframe) for promotion = human
seed via git merge. Does not supersede any prior ADR.
