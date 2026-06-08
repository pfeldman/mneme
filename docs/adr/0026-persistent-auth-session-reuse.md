# ADR-0026: Persistent authenticated-session reuse

Status: Proposed

## Context

Phase 3 made Praxis a pip-installable library that teaches, regresses, and
explores a live app through a browser (ADR-0019 through ADR-0025). The next
honest milestone is to run it against a REAL application rather than the toy
`testapp`, because the whole bet (operational knowledge outlives a procedure
cache) is only validated provisionally on the toy at Phase 1 (ADR-0010). The
first candidate real app is one a maintainer already works with day to day.

A real app blocks on its login. The canonical blocker is two-factor
authentication (2FA): after the password the app demands a second one-time
code. For the concrete target that motivated this ADR the second factor is an
email-delivered code (a code sent to a Gmail inbox), not an authenticator-app
code. A one-time code that arrives by email changes every login and cannot be
pre-stored, so a naive design would force a human to pass 2FA on every single
run, which makes a scriptable `praxis regress` and any CI gate impractical.

The building blocks already exist and constrain the design:

- ADR-0017 fixed that knowledge records only the ABSTRACT `auth_state`
  (`authenticated` plus `scope`), never the secret that produced it; the
  adapter-boundary validator rejects tokens, cookies, IDs, and PII.
- ADR-0021 fixed the secrets channel: credentials live outside committed
  knowledge in a gitignored `.praxis.secrets` file or an environment variable,
  an environment variable WINS over the file, and a missing credential is
  ask-or-fail by surface (the skill asks, the console and CI fail loudly). The
  raw per-machine run log under `.praxis/runs/` is gitignored.
- ADR-0022 fixed the teach credential prompt: a human types a secret live, it
  drives the browser for the session only, and it is never persisted.
- ADR-0023 fixed the regress verdict OK / REGRESSED / STALE (the break-vs-drift
  distinction), and that a non-OK goal is loud, never silently green.
- ADR-0024 fixed that CI is the team's CI: secrets are runner secrets read from
  the environment, never committed or echoed.

The browser runtime (Playwright) can persist an authenticated session as a
"storage state" (the cookies and local storage that prove a logged-in browser).
Reusing that saved session across runs lets a goal run authenticated WITHOUT a
fresh login, hence without a fresh 2FA. The open question this ADR answers is
how that saved session fits the Praxis secret and verdict model: where it
lives, why it is never knowledge, how CI gets it when it cannot be pushed to
the repo, and what a run does when the saved session has expired.

## Decision

### 1. Praxis may persist and reuse the authenticated browser session.

Praxis may save the Playwright authenticated session (the storage state: the
cookies and local storage of a logged-in browser) and reuse it on later teach,
regress, and explore runs so a goal runs authenticated without a fresh login.
Reusing a saved session is what removes the per-run 2FA cost and makes a
real-app login goal scriptable. The session is an operational input to the
browser, exactly like a password; it is not a new kind of knowledge.

### 2. The saved session is a secret; it is never knowledge.

The saved session contains live session cookies and tokens, so it IS a secret
of the same class as a password. It is treated exactly like the ADR-0021
`.praxis.secrets` channel: gitignored, never committed, and never written into
any committed file under `.praxis/` (no knowledge file, no candidate file, no
committed run artifact). Knowledge continues to record ONLY the ADR-0017
abstract `auth_state` (`authenticated` plus `scope`); the session itself, its
cookies, its tokens, and any 2FA code never cross into an emitted assertion.
The adapter-boundary validator (ADR-0017, ADR-0022 decision 5) stays the
runtime defense. The session is an auth artifact stored beside knowledge, never
inside it.

### 3. The session lives in the secrets channel; an environment / CI secret wins over the local file.

The session uses the same channel split ADR-0021 fixed for credentials. Local
use stores the session as a gitignored file (a sibling of `.praxis.secrets`,
never inside the committed tree); CI use supplies the session from the CI
secret store (a runner secret, for example a GitHub Actions secret) injected
into the runner at job start, and an environment / runner secret WINS over the
local file. This resolves the apparent contradiction "if the session is never
pushed, how does CI use it": the session is never pushed to the REPOSITORY, but
it is supplied to CI through the CI's encrypted secret store, which is not the
repository. The repo stays clean; the secret lives in the vault and the runner
injects it. A missing session follows the ADR-0021 ask-or-fail behavior by
surface.

### 4. One saved session per role, reused across that role's goals.

A saved session is keyed by the ABSTRACT role it authenticates as (the ADR-0017
`auth_state.scope`: `anonymous`, `user`, `admin`, or a SUT-specific role), not
by an individual goal. All goals that target the same role reuse the same saved
session; a goal does not get its own session. This keeps the number of stored
secrets proportional to the number of roles under test, not to the number of
goals, and matches the ADR-0017 model where scope, not goal, is the unit of
authentication.

### 5. Refresh-on-expiry is a distinct AUTH-EXPIRED outcome, never a false REGRESSED.

A run that detects the saved session is expired or invalid (a redirect to the
login page, or the believed success signals not firing because the browser is
logged out) MUST NOT report it as REGRESSED (the app broke) or STALE (the
knowledge is outdated). It is a distinct THIRD condition, AUTH-EXPIRED,
reported alongside the ADR-0023 OK / REGRESSED / STALE verdict. AUTH-EXPIRED is
not a bug in the app and not stale knowledge; it is "the run could not
authenticate". By surface:

- On the Claude Code skill surface (a human is present): the run asks the human
  to re-authenticate, the human passes 2FA once through the ADR-0022 teach
  credential prompt, and the refreshed session is re-saved to the secret
  channel.
- On the console / CI surface (no human): the run fails LOUDLY naming
  AUTH-EXPIRED and the role whose session expired, with a non-zero exit, never
  silently green and never a false REGRESSED. A human then refreshes the CI
  secret (decision 7). Misclassifying an expired session as a regression would
  cry wolf on the gate; misclassifying it as green would hide a broken check.
  AUTH-EXPIRED keeps both loud and correctly named.

### 6. The 2FA flavor sets the refresh cost; both costs are stated honestly.

How a session is refreshed depends on the second factor, and the cost is
recorded so it is not a surprise:

- An EMAIL-delivered one-time code (the motivating case) cannot be refreshed
  autonomously in CI, because CI has no human and no inbox to read the code
  from. The refresh is therefore a periodic HUMAN action: a human re-logs in
  locally, passes the email 2FA, and updates the CI secret with the new
  session. CI reuses the stored session until it expires, then surfaces
  AUTH-EXPIRED (decision 5) until a human refreshes it.
- A TOTP authenticator-app second factor (a code generated from a fixed shared
  seed) CAN be refreshed autonomously: the seed is a storable secret, and the
  runner can regenerate the current code from it and log in fresh, so CI can
  self-refresh without a human. Email-OTP cannot do this; the difference is the
  storable seed.

This ADR does not require the target app to switch to TOTP; it records that
email-OTP carries a manual-refresh operating cost and TOTP removes it, so the
choice is explicit.

### 7. The interactive teach login bootstraps the reusable session.

The first saved session is produced by a human teach login (ADR-0022): a human
runs teach, logs in once, passes 2FA live (the code drives the browser and is
never persisted, ADR-0022 decision 5), and the resulting authenticated session
is saved to the secret channel (decision 3). That human-in-the-loop login is
the legitimate seed of the reusable session, the same way the teach human
confirm is the legitimate seed of the oracle (ADR-0005). There is no autonomous
way to mint the first session, because minting it requires passing 2FA, which
requires the human.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Commit the saved session to git, or write it into any file under `.praxis/`
  (no knowledge file, no candidate file, no committed run artifact). The
  session is a secret; it lives in the gitignored local file or the CI secret
  store only.
- Record the session, its cookies, its tokens, or the 2FA code into knowledge.
  Knowledge records only the ADR-0017 abstract `auth_state`; the
  adapter-boundary validator rejects the secret shapes.
- Report an expired session as REGRESSED or as a green OK. An expired session
  is the distinct AUTH-EXPIRED outcome (decision 5); collapsing it into either
  is the loud-vs-silent failure this project exists to avoid.
- Run the teach operation in CI to bootstrap a session. Teach is skill-only and
  human-in-the-loop (ADR-0019, ADR-0022); a CI teach would have no human to
  pass 2FA and would break the seed rule. The session is bootstrapped locally
  and supplied to CI as a secret.
- Force-push `.praxis/`. The committed history stays the append-only analog
  (ADR-0021); the session lives outside it and changes nothing about that rule.

## Consequences

Positive:

- A real app whose login needs 2FA becomes testable: a human passes 2FA once to
  seed the session, and every later run reuses it with no 2FA. This unblocks
  the first honest real-app validation the project needs.
- The session reuses the ADR-0021 secret model unchanged, so the "if it is not
  pushed, how does CI use it" question has a clean answer: the CI secret store,
  not the repo. No new secret-handling concept is introduced; the session is
  one more item on the existing channel.
- The AUTH-EXPIRED outcome keeps the regress gate honest. An expired session
  neither cries wolf as a regression nor hides as green; it is named and loud,
  and a human knows exactly what to refresh.

Negative:

- For an email-OTP second factor the session refresh is a recurring human
  chore: when the stored session expires, a person must re-login locally and
  update the CI secret. The frequency is set by the app's session lifetime, not
  by Praxis. TOTP removes the chore but requires the test account to use an
  authenticator-app factor.
- A saved session is a high-value secret: anyone with the session file or the
  CI secret is logged in as that role without a password or a 2FA. The blast
  radius is bounded by using a dedicated low-privilege test account (no access
  beyond a normal user) and by the session's own expiry, but the secret is real
  and must be guarded like any other credential.
- The implementation adds a third verdict path (AUTH-EXPIRED) the regress engine
  and both surfaces must handle, a small increase in the surface ADR-0023
  defined.

Invariants respected:

- `no-secrets-tokens-pii-in-knowledge`: the session is a secret stored in the
  gitignored channel or the CI secret store, never in a committed `.praxis/`
  file; knowledge records only the ADR-0017 abstract `auth_state`.
- `loud-and-traceable-over-silent-and-convenient`: an expired session is the
  named, non-zero AUTH-EXPIRED outcome, never a silent green and never a false
  REGRESSED; the refresh action is explicit by surface.
- `append-only-store-no-mutation`: the session lives outside the committed
  store and changes nothing about the append-only history; force-push stays
  forbidden (ADR-0021).
- `first-oracle-must-be-seeded` (by analogy): the reusable session is seeded by
  a human teach login that passes 2FA, the same human-in-the-loop seed posture
  ADR-0022 uses for the oracle; there is no autonomous session minting.

Invariants this ADR does NOT cover:

- The implementation mechanics: the exact on-disk session-file format and
  location, the storage-state read/write wiring through the ADR-0003 adapter,
  the regress-engine detection of an expired session, and the AUTH-EXPIRED
  surfacing in the report and exit code. These are a separate implementation
  task; this ADR fixes the decisions, not the code.
- The choice of which real app to validate against and its test-account policy.
  This ADR enables the capability; selecting the target and provisioning a
  low-privilege test account is an operational decision outside it.

## Relation to prior ADRs

Extends ADR-0017 (auth_state abstract posture, Accepted): the saved session is
the concrete authenticated artifact whose ABSTRACT posture (`authenticated`
plus `scope`) is the only thing knowledge records; the session itself never
crosses the adapter boundary into an assertion.

Extends ADR-0021 (the secrets channel, Accepted): the session is a new item on
the same channel, with the same gitignored-local-file-versus-CI-runner-secret
split and the same environment-wins-over-file precedence; it is never committed
and never inside the `.praxis/` tree.

Extends ADR-0022 (the teach credential prompt, Accepted): a human teach login
that passes 2FA live (the code never persisted) is what bootstraps the reusable
session; the credentials-never-persisted rule carries over to the 2FA code.

Extends ADR-0023 (the regress verdict, Accepted): adds AUTH-EXPIRED as a third
outcome distinct from OK / REGRESSED / STALE, preserving the loud-not-silent
contract for an expired session.

Builds on ADR-0024 (CI is the team's CI, Accepted): the session is supplied to
CI as a runner secret read from the environment, exactly like the API key,
never committed and never echoed. Does not supersede any prior ADR.
