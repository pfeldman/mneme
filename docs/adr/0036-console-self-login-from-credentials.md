# ADR-0036: Console self-login from credentials when no usable session (MFA-free)

Status: Proposed

## Context

ADR-0026 made Praxis reuse a saved authenticated browser session (the Playwright
storage state) so an authenticated goal runs WITHOUT a fresh login, hence
without a fresh 2FA. ADR-0027 made the bare console runner self-driving through a
local `claude -p` brain, and fixed that an authenticated PRECONDITION goal (the
login is setup, not the subject under test) reuses that saved session while an
auth-SUBJECT goal (`auth_state.being_tested`) performs a real login every run.
ADR-0035 scoped both the session and the credential channels per environment.

On the console path the brain only ever REUSES a saved session. `resolve_storage_
state` (`cli/claude_brain.py`) resolves the role's storage state from the env var
`PRAXIS_AUTH_STATE_<ENV>_<ROLE>` or the file `.praxis.auth/<env>/<role>.json`,
injects it via a synthesized `--storage-state` MCP config, and runs. When no
usable session is found it short-circuits to `_auth_expired_observations` WITHOUT
driving the browser, and the classifier routes the goal to the loud AUTH-EXPIRED
verdict (the goal expected an authenticated scope but no session was usable).

That short-circuit is the blocker for unattended CI. Saved sessions expire, so a
scheduled `praxis regress` hits AUTH-EXPIRED the moment its session ages out, and
on the console surface there is no human to re-seed it (ADR-0026 decision 5). The
only place credential login exists today is the human-in-the-loop skill surface
(teach), where a human types the 2FA code.

For a dedicated low-privilege QA test account with **MFA disabled**, a username +
password login needs no interactive code and can be fully automated. The console
brain ALREADY carries the instruction to do this: the `_HEADLESS_PREAMBLE`
already tells the agent "if no saved session was injected, read credentials from
`.praxis.secrets` and type them into the login form", and already tells it to
STOP if it hits a 2FA wall. The agent never gets the chance, because the brain
returns AUTH-EXPIRED before driving the browser at all.

This ADR fixes how the console runner self-logs-in from credentials when there is
no usable session, so an MFA-free account can run a scheduled regress with no
human present, while keeping every existing contract (the AUTH-EXPIRED contract
for the no-credential case, the redaction invariants, the exit-code semantics)
intact.

## Decision

### 1. With no usable session but resolvable credentials, the console runner self-logs-in instead of returning AUTH-EXPIRED.

When the console brain is about to fail with AUTH-EXPIRED for a precondition
role (no session, or the injected session is rejected as expired) AND credentials
for that role are resolvable from the existing secret chain (env var >
`.praxis.secrets.<env>` overlay > `.praxis.secrets`), the brain DRIVES the
browser for a fresh login instead of short-circuiting. The agent navigates to the
selected environment's `base_url` (already injected into the per-goal prompt as
the "App under test:" line, ADR-0035 decision 3), types the resolved username and
password into the login form, and proceeds with the goal in the SAME browser
context. There is no separate "capture storage state and re-inject" step: the
login happens in the live context that then runs the goal, so the freshly
authenticated session is simply the context the run continues in.

This is strictly opt-in and additive:

- ONLY when credentials for the role are present. With NEITHER session NOR
  credentials the brain keeps returning AUTH-EXPIRED exactly as today (decision
  4); the contract for the no-credential case is unchanged.
- ONLY on a precondition role. An auth-SUBJECT goal (`being_tested`) already
  performs a real login every run (ADR-0027 decision 2); an anonymous /
  non-authenticated goal needs no login. Neither path changes.

### 2. The self-login is the existing natural-language login routine, not a scripted Playwright routine.

Praxis describes the login in natural language and the agent executes it through
the Playwright MCP; Praxis itself never drives Playwright directly and never
hardcodes an app's login selectors (the teach `SKILL.md` "log in like a tester
would" routine and the console `_HEADLESS_PREAMBLE` are both pure NL). The
console self-login reuses THAT routine: the brain unblocks the drive and the
agent logs in freeform, exactly as the teach human-present login does, minus the
human (the account is MFA-free, so there is no code to type).

A deterministic Python/Playwright login routine (navigate, fill, submit by fixed
selectors) is the rejected alternative: it would couple Praxis to each app's
login DOM, which the project deliberately avoids, and it does not match how
Praxis drives any browser. The shared "routine" is shared PROMPT TEXT, surfaced
identically on both surfaces, not shared imperative code.

### 3. Credentials resolve per role, generalizing the existing key convention.

The credential keys generalize the per-role shape so any role `<role>` resolves
`APP_USERNAME_<ROLE_UPPER>` / `APP_PASSWORD_<ROLE_UPPER>`, with the `user` role
keeping the unsuffixed `APP_USERNAME` / `APP_PASSWORD` for back-compatibility
with the existing convention. Per-KEY resolution is unchanged and stays the
ADR-0021 / ADR-0035 chain: env var > `.praxis.secrets.<env>` overlay >
`.praxis.secrets`. The brain checks PRESENCE of both keys to decide self-login vs
AUTH-EXPIRED, and names the exact keys for the run's role in the self-login
prompt directive so the agent reads the right pair; the credential VALUES are
read by the AGENT from the file into the browser, never by the Python layer into
any logged surface.

### 4. No credentials -> AUTH-EXPIRED, unchanged.

When a precondition role has neither a usable session nor resolvable credentials,
the brain returns the same `_auth_expired_observations` it returns today, naming
the role (and the selected environment, ADR-0035 decision 7), and the classifier
routes the goal to AUTH-EXPIRED with a non-zero exit. The self-login path is an
ADDITION strictly gated on credential presence; the absence path is byte-for-byte
the pre-ADR-0036 behavior.

### 5. MFA-free only; a 2FA / OTP prompt mid-login is a loud stop naming role + env.

If the login flow presents a 2FA / one-time-code prompt (the account is
misconfigured, MFA is not actually off), the agent STOPS and emits what it
observed with `authenticated: false`, exactly as the `_HEADLESS_PREAMBLE` already
instructs for an emailed 2FA wall. It NEVER reads an inbox, never attempts to
solve a challenge (the same rule the skills follow). A self-login that cannot
complete because of a 2FA prompt routes to AUTH-EXPIRED (reusing the ADR-0027
decision 3 routing for a headless 2FA block), with the evidence naming the role
AND the environment so the operator knows exactly which account is misconfigured.
It is loud and non-zero, never a false green and never a false REGRESSED.

### 6. Credentials never persist outside their channel; the run stays redaction-safe.

A credential never lands in knowledge, candidates, runs, reports, logs, or
chat / agent output. The agent is instructed (the shared NL routine) never to
WRITE a credential, cookie, token, session id, or 2FA code into any file under
`.praxis/`; knowledge records only the ADR-0017 abstract `auth_state`. The
captured live session is subject to the existing adapter-boundary redaction and
the `auth_state`-only rule, and the `AuthStateLeakError` boundary still holds.
The session minted by a console self-login is NOT persisted to
`.praxis.auth/<env>/<role>.json`: the CI posture is to log in fresh each run (the
motivating decision), and autonomous session persistence stays reserved for the
human teach seed (ADR-0026 decision 7). This is a hard invariant, not a default.

### 7. Headless-first; the interactive skill login is unchanged.

The capability lives on the bare `claude -p` console path used by CI, not only
the interactive skill path. The interactive skill login flow (teach, which
already does credential + interactive 2FA login with a human present) is
unchanged. The exit-code contract is otherwise unchanged: OK / REGRESSED / STALE
/ ERROR / AUTH-EXPIRED keep their meanings.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Drive a logged-out browser and let the goal produce a false REGRESSED when a
  precondition role has no session and no credentials. That case stays
  AUTH-EXPIRED (decision 4).
- Read the credential VALUES into the Python brain's logged surfaces, or write a
  credential / cookie / token / 2FA code into any `.praxis/` file (decision 6).
- Attempt to solve, read, or wait on a 2FA / OTP challenge on the console path,
  or read an inbox for an emailed code. A 2FA prompt is a loud stop (decision 5).
- Hardcode an app's login selectors or drive Playwright directly from Praxis. The
  login is the NL routine the agent executes (decision 2).
- Persist a console-minted session to the `.praxis.auth/` channel automatically.
  The session seed stays the human teach login (decision 6, ADR-0026 decision 7).
- Change the oracle, the verdict classification, or the candidate / promotion
  logic. This ADR touches only how the console brain obtains an authenticated
  browser before running the goal.

## Consequences

Positive:

- A scheduled `praxis regress` against an MFA-free QA account runs unattended:
  it logs in fresh from credentials each run, so an expired session is no longer
  a babysitting event. This is the unblocker for wiring Praxis into a team's CI
  on a schedule.
- The change reuses every existing channel: the ADR-0021 / ADR-0035 credential
  chain, the ADR-0027 NL login routine, the ADR-0026 AUTH-EXPIRED contract, the
  ADR-0017 redaction boundary. No new secret-handling concept is introduced.
- The no-credential and 2FA-blocked cases stay loud and correctly named, so the
  regress gate keeps its honesty: a missing credential and a misconfigured-MFA
  account both surface as a named, non-zero AUTH-EXPIRED, never a false green.

Negative:

- An MFA-free account is a real security posture choice: the login principal must
  be a dedicated, low-privilege, no-SSO test account, because a stored
  username + password with no second factor is enough to log in as that role.
  The blast radius is bounded by the account's privilege, the same bound ADR-0026
  already states for a stored session.
- A fresh login every run is slower than reusing a warm session and exercises the
  login flow on every scheduled run. That is the explicit trade for not shipping
  expirable session files; the per-run login cost is accepted.

Invariants respected:

- `no-secrets-tokens-pii-in-knowledge`: credentials and the live session stay in
  their gitignored / runner-secret channels; knowledge records only the abstract
  `auth_state`; the adapter-boundary validator and `AuthStateLeakError` are
  unchanged (decision 6).
- `loud-and-traceable-over-silent-and-convenient`: no-credential and 2FA-blocked
  self-logins are named, non-zero AUTH-EXPIRED outcomes, never a silent green and
  never a false REGRESSED (decisions 4, 5).
- `append-only-store-no-mutation`: nothing is written to the committed store; the
  session is never persisted by the console path (decision 6).

Invariants this ADR does NOT cover:

- The exact prompt wording of the self-login directive and the 2FA-stop evidence
  text, and the precise placement of the credential-presence check. These are
  implementation detail fixed in the task plan, not here.
- The target app and its test-account provisioning (a dedicated MFA-free,
  no-SSO, no-VPN-gate account). This ADR enables the capability; provisioning the
  account is an operational decision outside it.

## Relation to prior ADRs

Extends ADR-0026 (persistent auth-session reuse, Proposed): adds a second way to
obtain an authenticated browser on the console path. Session reuse stays the
fast path; a fresh credential self-login is the fallback when no session is
usable but credentials are. The AUTH-EXPIRED outcome is narrowed to "no session
AND no credentials" on the console surface.

Extends ADR-0027 (console runner and `claude -p` brain, Accepted): the self-login
unblocks the `resolve_storage_state` short-circuit and reuses the
`_HEADLESS_PREAMBLE` fresh-login routine and the headless 2FA-block-as-AUTH-
EXPIRED routing (decision 3). The auth-subject vs precondition split is unchanged.

Extends ADR-0021 (the secrets channel, Accepted) and ADR-0035 (multi-environment,
Proposed): generalizes the credential KEY convention per role
(`APP_USERNAME_<ROLE>`) while keeping per-key resolution (env > overlay > base)
and per-env `base_url` exactly as those ADRs fixed them.

Builds on ADR-0022 (teach credential prompt, Accepted): the console self-login is
the headless, MFA-free analog of the teach human login; the credentials-never-
persisted rule carries over unchanged. The interactive teach flow is not altered.
Does not supersede any prior ADR.
