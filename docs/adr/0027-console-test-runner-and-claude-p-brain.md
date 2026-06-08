# ADR-0027: The self-contained console test runner, the claude -p local brain, and auth-as-subject vs auth-as-precondition

Status: Proposed

## Context

ADR-0019 fixed two brains: the local brain is Claude Code via skills (no API
key, on the user's subscription, a human present and triaging) and the CI brain
is an API-key agent from the `live` extra (autonomous, no human, paid).
ADR-0023 gave regress and explore a dual surface over the same engine (a console
CLI with a process exit code, and a Claude Code skill that adds interactive
triage) and the OK / REGRESSED / STALE break-vs-drift verdict. ADR-0026 added
session reuse (save the authenticated browser session once, reuse it to skip the
per-run login and 2FA) and the AUTH-EXPIRED fourth outcome. ADR-0017 fixed
`auth_state` (`authenticated` plus `scope`) as the only auth posture knowledge
records.

Two facts from dogfooding the library against a real app (a Digioh login goal,
prod, live email 2FA) drive this ADR.

Finding A: the console surface is not actually self-driving. Running bare
`praxis regress` from a terminal prints a prompt and HANGS on stdin waiting for
an agent to paste back its observations (the Phase-1 paste executor,
`_executor_from_paste` in `cli/main.py`). It never drives the browser itself. To
a human that ran `praxis regress` expecting a test run, a process that prints a
wall of prompt text and blocks on stdin reads as broken. The console surface
ADR-0023 named test-style red / green has no brain wired into it; the only
brains ADR-0019 fixed are the interactive skill and the API-key CI agent, and
neither is "drive the browser locally from a console with no human watching and
no API key".

Finding B: a login goal cannot be regression-tested through session reuse. The
dogfood login goal `login-reach-campaigns-dashboard` declares
`auth_state: {authenticated: true, scope: user}` and its success signal IS
reaching the authenticated dashboard. Session reuse (ADR-0026) makes a run
authenticated WITHOUT performing the login, which is exactly right for a feature
goal where logging in is setup, but it is wrong for a login goal: reusing a
saved session would make the login goal pass by skipping the login it is
supposed to test. A feature goal ("create a campaign") and a login goal both
carry `auth_state: {authenticated: true, scope: user}`, so `auth_state` alone
cannot tell them apart; the goal must declare whether authentication is the
subject under test or merely a precondition.

This ADR owns three coupled decisions that these findings force: how a goal
declares auth-as-subject vs auth-as-precondition and what each implies for
session reuse; the third execution path that makes the console surface
self-driving (a local `claude -p` brain, distinct from the interactive skill and
the API-key CI agent); and the pytest-style console experience that path
delivers. It does not re-derive the verdict contract (ADR-0023), the session
secret model (ADR-0026), or the CI wiring (ADR-0024).

## Decision

### 1. A goal declares whether authentication is the subject under test or a precondition.

A goal carries an explicit declaration of its relationship to authentication.
The default is PRECONDITION: authentication is setup the goal needs but is not
the thing under test. A goal may instead declare authentication is the SUBJECT:
the login flow itself is what the goal verifies. The declaration is a new
optional field on the ADR-0017 `auth_state` object
(`auth_state.being_tested`, a boolean defaulting to false), co-located with the other
auth fields because it is intrinsically about authentication and reads as "is
this auth_state the thing under test". Absent or false means precondition; true
means subject. Activating this field is an additive schema change that carries
the model-vs-schema agreement test (AGENTS.md); it is NOT one of the deferred
`states` / `paths` fields and does not reopen the `refuted` status.

### 2. An auth-subject goal performs a real login; a feature goal reuses the saved session.

The declaration in decision 1 selects the session behavior:

- A goal where authentication is the SUBJECT does NOT reuse a saved session. It
  performs a real login every run, because the login IS the test; reusing a
  session would skip the very flow under test and make the goal pass without
  exercising it. This is the honest reading of ADR-0026: session reuse removes
  the per-run login cost for goals where the login is incidental, never for a
  goal whose success signal is the login working.
- A goal where authentication is a PRECONDITION reuses the saved session
  (ADR-0026): the login is setup, not the test. If no saved session exists, the
  run logs in once as setup to establish the session; that login is setup, never
  counted as the test, and the resulting session is saved for reuse (ADR-0026
  decision 7).

Knowledge still records only the ADR-0017 abstract `auth_state`; this decision
changes only whether a run reuses or re-performs the login, never what an
assertion stores.

### 3. regress and explore become self-contained console test runners driven by a local claude -p brain.

The console surface of `praxis regress` and `praxis explore` drives itself. It
shells out to `claude -p` (Claude Code in headless / print mode) as the local
brain: the SAME Claude Code subscription reasoning the interactive skill uses, on
NO API key, delivered through a headless console process instead of an
interactive session. The console runner gives `claude -p` a Playwright MCP so it
can drive the browser, passes it the per-goal prompt the engine already builds
(the ADR-0023 / ADR-0019 brain seam), and captures the agent's observations back
as JSON in the SAME shape the paste and `--from-file` executors already consume
(`{observations, actions, tokens, visited_urls}`). This is a THIRD execution
path: the local Claude Code brain gains a headless console surface alongside its
interactive skill surface, and it stays distinct from the API-key CI agent
(ADR-0019 section 3), which remains the brain for autonomous CI where there is no
subscription session to borrow. No new brain is baked into the core; the
`claude -p` path plugs in at the same brain seam as the other executors
(ADR-0019, brain-agnostic core).

### 4. One claude -p invocation per goal, run concurrently up to a bounded cap, each bounded by the ADR-0023 per-goal budget; a failed invocation is a loud per-goal ERROR.

The console runner spawns ONE `claude -p` invocation per goal, not one shared
session across all goals. Per goal matches the ADR-0023 decision 7 per-goal
budget: each goal gets its own token-and-wall-time slice, so one pathological
goal cannot starve the rest, and the budget boundary is also the process
boundary. The per-goal wall-time slice maps to a subprocess timeout. A
`claude -p` invocation that exits non-zero, times out, exceeds its budget slice,
or returns output that does not parse as the expected observation JSON is a loud
per-goal ERROR (ADR-0023 decision 4): it is named, it fails the run with a
non-zero exit, and it is NEVER silently counted as OK and never dropped. One
process per goal also isolates failures: a hang on goal 3 cannot kill goals
4..N, each runs in its own bounded invocation. Sharing one long session across
goals is forbidden (it would blur the per-goal budget, leak one goal's context
into another's verdict, and let one hang abort the rest).

Because each goal already runs as its own bounded process with its own browser
context, the per-goal invocations run CONCURRENTLY, not strictly one at a time,
the way `pytest-xdist` runs test files in parallel. Concurrency is capped by a
configurable limit (a `--jobs N` flag); `--jobs 1` is the sequential degenerate
case. The cap exists because the binding constraint is the Claude Code
subscription's rate limit, not the machine: an unbounded fan-out of `claude -p`
processes would hit the subscription rate limit, so the runner bounds
concurrency to a conservative default rather than launching all goals at once.
Parallelism changes only the SCHEDULING of the per-goal invocations; it does not
change the per-goal budget, the failure isolation, or the loud-ERROR contract,
which are all defined per goal and are independent of how many run at once. Two
constraints bound what may run in parallel: the saved per-role session
(ADR-0026 decision 4) is read concurrently by feature goals with no contention
because reading a session file is read-only; but two auth-SUBJECT goals
performing a real login against the same test account concurrently can collide
or trip a login rate limit, so auth-subject goals may be serialized rather than
run with the feature-goal fan-out.

### 5. The console runner runs headless by default, with a --headed flag.

The console runner drives the browser HEADLESS by default: the user runs
`praxis regress` and reads the result, they do not watch the browser. A
`--headed` flag shows the browser for debugging. Headless is the default because
the whole point of the console surface is a hands-off test run; the interactive
skill surface (ADR-0023) is where a human watches and triages.

### 6. The console experience is pytest-style: progress, live pass count, one final summary.

The console runner presents a test-runner experience, not a wall of prompt text
on stdin. It prints that it is running, shows per-goal progress (a spinner or a
running line) as each goal completes, shows a live "X / N passed" count, and ends
with a final summary listing each goal's OK / REGRESSED / STALE / AUTH-EXPIRED
(or ERROR) verdict and a roll-up. The verdict contract, the loud non-zero exit on
any REGRESSED / ERROR / AUTH-EXPIRED, and the markdown report under
`.praxis/runs/<timestamp>/` are exactly ADR-0023 and ADR-0026; this decision
fixes only the human-facing presentation of those verdicts as a pytest-style run.

### 7. The claude -p brain is the default local console brain; --from-file stays; the paste prompt is retired as the default; the CI API-key brain is unchanged.

The default brain for a console `praxis regress` / `praxis explore` invoked with
no `--goal`-less special flag and with `claude` available on PATH is the
`claude -p` brain (decision 3). The hanging interactive paste-on-stdin executor
(Finding A) is RETIRED as the default. The `--from-file` executor stays: it is
deterministic and is what the regression-recall experiment harness and the test
suite drive, and what a script that already has agent output feeds in. The CI
API-key brain (ADR-0019 section 3, ADR-0024) is UNCHANGED: autonomous CI still
uses the `live` agent because CI has no subscription session. The three paths
coexist by where they run: interactive skill (human present), `claude -p` console
(local, subscription, hands-off), API-key agent (CI, no subscription). They are
the same body and the same verdicts; only the driving brain and the surface
differ.

### 8. An auth-subject goal with a non-autonomous second factor cannot complete in the headless console runner; it surfaces loudly.

A goal where authentication is the SUBJECT (decision 1) performs a real login
(decision 2), and a real login may demand a second factor. An email-delivered
one-time code cannot be passed by the headless `claude -p` console runner: there
is no human and no inbox to read the code from, exactly the ADR-0026 decision 6
constraint. Such a goal in the console runner surfaces LOUDLY as a non-OK outcome
naming that it needs a human-present surface; it is never silently green and
never a false REGRESSED. It routes to the interactive skill surface, where a
human passes 2FA once (ADR-0022), or to a TOTP authenticator-app second factor
whose seed is storable and lets the runner self-complete the login (ADR-0026
decision 6). A feature goal (precondition) is unaffected: it reuses the saved
session and runs headless with no login. This ADR does not require the target app
to switch to TOTP; it states the cost honestly so the limit of headless
auth-subject testing is explicit.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Reuse a saved session for an auth-SUBJECT goal. The login is the test; reusing
  a session would skip the flow under test and make the goal pass without
  exercising it (decision 2).
- Bake the `claude -p` brain into the core or make the core depend on Claude
  Code. The brain plugs in at the ADR-0019 brain seam as one more executor; the
  core stays brain-agnostic and tests with no `claude` binary present.
- Force an API key for the local console runner. The `claude -p` path is the
  subscription brain with no API key; the API-key agent stays reserved for CI
  (ADR-0019, ADR-0024).
- Run all goals in one shared `claude -p` session. One bounded invocation per
  goal preserves the per-goal budget, isolates failures, and stops cross-goal
  context leakage (decision 4).
- Fan out an unbounded number of concurrent `claude -p` processes. Concurrency
  is capped by a `--jobs` limit because the subscription rate limit, not the
  machine, is the binding constraint (decision 4); auth-subject goals are not run
  with the feature-goal fan-out when concurrent logins would collide.
- Count a failed, timed-out, budget-exhausted, or unparseable `claude -p`
  invocation as OK, or drop it silently. It is a loud per-goal ERROR that fails
  the run (decision 4, ADR-0023 decision 4).
- Report an auth-subject goal blocked on an email 2FA code as REGRESSED or as a
  green OK in the headless runner. It is a loud non-OK that routes to a
  human-present surface (decision 8); collapsing it into either is the
  loud-vs-silent failure this project exists to avoid.
- Record the `claude -p` brain choice, the session, the 2FA code, or any token
  into knowledge. Which brain ran is execution provenance at most (ADR-0019); the
  session and the code are secrets (ADR-0026); knowledge records only the
  abstract `auth_state` (ADR-0017).

## Consequences

Positive:

- The console surface becomes what ADR-0023 named it: a self-driving test
  runner. A human runs `praxis regress`, the run drives the browser headless on
  their subscription, and they read a pytest-style summary. Finding A (the
  hanging stdin prompt) is removed as the default.
- A login goal is testable as a regression check for the first time. Declaring
  authentication the subject (decision 1) makes the run perform a real login
  instead of reusing a session, so the login flow is actually exercised every
  run; Finding B is resolved without weakening session reuse for feature goals.
- The local console brain costs nothing beyond the existing Claude Code
  subscription, the same adoption posture ADR-0018 and ADR-0019 bet on. The
  hands-off console run no longer needs a human to paste, and it needs no API
  key.
- The three execution paths share one body and one verdict contract. The
  `claude -p` path plugs in at the existing brain seam, so the engine logic is
  not forked; only the driving brain and the surface differ.
- Running the per-goal invocations concurrently (decision 4) cuts the wall-clock
  of an aggregate run from the sum of all goals to roughly the slowest goal times
  the number of waves, the same speedup `pytest-xdist` gives a test suite, while
  the per-goal budget and the loud-ERROR contract stay intact.

Negative:

- One `claude -p` invocation per goal makes a console run's subscription cost
  scale linearly with the number of believed goals. The per-goal budget
  (ADR-0023 decision 7) caps a single goal, but the total grows with the goal
  count and is bounded by the subscription's rate limits and quota, not by
  dollars per token. Scoping a run to a subset stays a `--goal` invocation.
- Concurrency (decision 4) is capped by the subscription rate limit, not the
  machine, so a large believed set does not scale down to a single wave; the
  `--jobs` cap trades wall-clock against the rate limit and the right default is
  an operational choice the build settles. Auth-subject goals may have to be
  serialized to avoid concurrent logins colliding on the same test account, so
  they do not get the full feature-goal speedup.
- The `claude -p` integration is the novel and risky part: shelling out, wiring
  a Playwright MCP, passing the per-goal prompt, capturing the observation JSON,
  and handling a non-zero exit / timeout / malformed output. It must be proven
  end-to-end against the real dogfood goal headless before this ADR is marked
  Accepted; the decisions here fix the contract, not the wiring.
- An auth-subject goal with an email 2FA cannot run in the headless console
  runner at all (decision 8). The headless runner covers feature goals (session
  reuse) and auth-subject goals only when the second factor is autonomous (TOTP)
  or a human is present (the skill surface). This is a real coverage limit of the
  hands-off path, stated rather than hidden.
- A third execution path is a third thing to keep working. A change to the
  agentic operations must now be exercised as an interactive skill, as a
  `claude -p` console run, and as a `--from-file` / CI run; this widens the
  dual-surface test burden ADR-0019 and ADR-0023 already flagged.

Invariants respected:

- `brain-agnostic-core` (ADR-0019): the `claude -p` brain plugs in at the same
  brain seam as the paste and `--from-file` executors; the core imports and tests
  with no `claude` binary and no LLM present, and knowledge never records which
  brain ran.
- `no-silent-success-when-app-broken` (ADR-0023): a failed `claude -p`
  invocation, a budget-exhausted goal, and an auth-subject goal blocked on email
  2FA are each a loud, named, non-OK outcome that fails the run; none is counted
  OK or dropped.
- `loud-and-traceable-over-silent-and-convenient`: the auth-subject-vs-
  precondition declaration is explicit on the goal, the session behavior follows
  from it deterministically, and the headless-auth-subject limit is named, not
  silently worked around.
- `no-secrets-tokens-pii-in-knowledge` (ADR-0017, ADR-0026): the session, the 2FA
  code, and the brain choice never cross into an assertion; knowledge records only
  the abstract `auth_state`.

Invariants this ADR does NOT cover:

- The OK / REGRESSED / STALE verdict contract, the per-goal budget rule, the
  loud non-zero exit, and the `.praxis/runs/<timestamp>/` report layout: owned by
  ADR-0023 (and ADR-0021 for the run dir). This ADR consumes them and fixes only
  the self-driving console brain and the pytest-style presentation.
- The session secret model (gitignored local file vs CI runner secret,
  environment-wins precedence), the AUTH-EXPIRED outcome, and the TOTP-vs-email
  refresh cost: owned by ADR-0026. This ADR consumes the session model and adds
  only the auth-subject-no-reuse rule and the headless email-2FA limit.
- The teach credential prompt and the human-seed protocol an auth-subject goal's
  first login uses: owned by ADR-0022. This ADR fixes only when a real login is
  performed, not the prompt protocol.
- The CI API-key brain and how a team wires the console commands into its own CI:
  owned by ADR-0019 and ADR-0024. This ADR leaves the CI brain unchanged.
- The exact `claude -p` invocation, the Playwright MCP config, the on-disk
  prompt and parse format, and the schema activation of `auth_state.being_tested`:
  implementation, proven against the dogfood goal in the build that follows this
  ADR's approval.

## Relation to prior ADRs

Extends ADR-0019 (brain pluggability and execution surfaces, Accepted): ADR-0019
named two brains (the local Claude Code skill and the CI API-key agent). This ADR
adds a third execution path for the SAME local Claude Code subscription brain, a
headless `claude -p` console surface, distinct from the interactive skill and
from the API-key CI agent. It plugs in at the same brain seam and keeps the core
brain-agnostic; it does not introduce an API key for local use.

Extends ADR-0023 (regress and explore dual surface and verdict, Accepted): makes
the console surface ADR-0023 named actually self-driving (Finding A) and fixes
its pytest-style presentation, while consuming the verdict contract, the per-goal
budget, and the loud non-zero exit unchanged.

Extends ADR-0026 (session reuse and AUTH-EXPIRED, Proposed): adds the
auth-subject rule (an auth-subject goal does NOT reuse a session; it performs a
real login, Finding B) on top of ADR-0026's reuse model, and carries ADR-0026
decision 6's email-vs-TOTP refresh cost into the headless console runner as the
auth-subject coverage limit (decision 8).

Extends ADR-0017 (abstract `auth_state`, Accepted): activates an additive
`auth_state.being_tested` field to declare auth-as-subject; knowledge still records
only the abstract posture, never the session or the code.

Re-cites ADR-0022 (the teach credential prompt) for the human login an
auth-subject goal performs, ADR-0024 (CI is the team's CI) for the unchanged CI
brain, and ADR-0021 for the run-dir report layout. It does not supersede any
prior ADR.
