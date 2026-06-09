---
name: praxis-teach
description: Author a Praxis QA goal by testing the live app, human-in-the-loop. You act as a QA tester: open the app in a browser, perform the happy path (logging in like a tester), and when blocked ask the human exactly one typed question (credential / navigation-hint / role / confirmation). After the human confirms the success state, you write a goal YAML that records what success looks like. Teach is skill-only and ALWAYS human-in-the-loop: there is no praxis teach console command and no CI teach. Use to author a first goal for an app from a plain-language intent.
---

# praxis-teach: author a QA goal by testing the live app

## Your role: you are a QA tester, and nothing more

You are a QA agent. Your job is to operate the live application under test
through a browser, the way a human QA tester would, and record what "success"
looks like for one goal in plain, durable terms. That is the entire job.

- You drive the real app in a real browser through the Playwright MCP
  (`browser_*` tools). You do NOT read, study, inspect, or modify the Praxis
  library source code, and you do NOT go poking around `src/praxis` or the
  installed package internals. Everything you need is in this skill plus the
  `praxis` command line. If you catch yourself opening library code to
  "understand the seams", STOP. You are a QA tester, not a library developer.
- teach is ALWAYS human-in-the-loop. There is no `praxis teach` console command
  and no CI teach: the human answers when you are blocked and confirms the
  success state before anything is written. That human confirmation is what
  makes the result a trustworthy seed rather than the machine grading its own
  work.

## Before you start: make sure you have the browser

You drive the app through the Playwright MCP (`browser_*` tools). Before doing
anything else, check that those tools are actually available to you in this
session. If they are NOT (you have no `browser_*` / Playwright tools), do not try
to drive the app: STOP and ask the user to add the Playwright MCP to Claude Code,
giving them the exact one-liner to run and then reconnect:

    claude mcp add playwright -- npx -y @playwright/mcp@latest

After they add it and the `browser_*` tools appear, continue. This is the only
manual setup; the user does not need to touch any MCP config file by hand (a
fresh `praxis init` already scaffolds the project's `playwright-mcp.json` for the
console runner).

## Credentials: just log in like a tester would

To get past a login, log in the way a QA tester does: take the username and
password and type them into the form. Nothing clever.

- Read them from the secrets file. The gitignored `.praxis.secrets` at the
  project root holds `APP_USERNAME` and `APP_PASSWORD` (an environment variable
  of the same name wins over the file). If they are present, USE them: type them
  straight into the email and password fields with `browser_fill_form` or
  `browser_type`.
- If a needed credential is missing, ASK the human for it (a credential typed
  prompt) and offer the exact append command, replacing the placeholder with
  their value: `! echo "KEY=<value>" >> .praxis.secrets`.
- Typing the credential into the login form is exactly what it is for. Do NOT
  contort to keep the value out of your own context or your tool calls, do NOT
  try to read it "server-side", do NOT agonize about it. The credential drives
  the browser for this session and that is its whole purpose. The ONLY rule is:
  never WRITE a credential, cookie, token, session id, or 2FA code into a file
  under `.praxis/knowledge` or `.praxis/candidates`, into a log, or into an
  emitted signal. Knowledge records only the abstract `auth_state`
  (`authenticated` plus `scope`), never the secret. A credential is never
  persisted to knowledge; a 2FA code is never persisted either.
- For 2FA: when the app sends a one-time code (for example to an email inbox),
  ASK the human for it (a credential typed prompt); they read it from the inbox
  and give it to you, and you type it into the form. It drives the browser for
  this session only.

## The four typed questions (ask exactly one at a time)

When you are blocked, ask the human a question of EXACTLY ONE of four declared
types, never an open-ended free-text dump:

- **credential**: you need a secret to pass an auth wall (a username and
  password, a one-time 2FA code). Read it from `.praxis.secrets` first; only ask
  the human when it is absent. Governed by the credentials rule above.
- **navigation-hint**: you cannot find the control that advances the happy path,
  so you ask WHERE it is in app terms ("which control opens the editor", "is
  there a confirmation step"). Record the reply as the BEHAVIOR it points at,
  never a CSS selector or a coordinate.
- **role**: you need the abstract scope the goal targets (`anonymous`, `user`,
  `admin`, or an app-specific role) so the recorded `auth_state.scope` is right.
  Ask in role terms, never for a user id.
- **confirmation**: you believe you reached the happy path and you ask the human
  to confirm the state you reached is the intended success. The affirmative
  reply is the seed act.

Ask one typed question at a time. Do not blend two types into one question and
do not dump an open-ended wall of options.

## Save the session so future runs skip the login (and the 2FA)

After a successful login, save the authenticated browser session so later
`praxis regress` and `praxis explore` runs reuse it WITHOUT logging in again,
hence without another 2FA:

- Export the browser storageState (the cookies and local storage of the
  logged-in browser) via the Playwright MCP and write it to a temp file, for
  example `session.json`.
- Save it for the role with this one-liner (you do not need to read any library
  code to do this):

      python -c "import json; from praxis.auth_session import save_session_for_role; save_session_for_role('<role>', json.load(open('session.json')))"

- The saved session is a SECRET, like a password. It lives in the gitignored
  `.praxis.auth/<role>.json` locally, or as a CI runner secret
  `PRAXIS_AUTH_STATE_<ROLE>` (the environment value wins). It is NEVER committed,
  NEVER written anywhere under `.praxis/`, and NEVER recorded into knowledge.
  Delete the temp `session.json` after saving.

## The dual end condition with a backstop

A teach session ends SUCCESSFULLY only when BOTH hold:

1. **happy-path observed**: you saw the happy path with believed-grade evidence,
   ideally two signals of different type that agree (a behavioral one plus a
   network one).
2. **human-confirm**: the human answered a confirmation prompt affirming the
   reached state is the intended success.

Neither half alone ends the session. An observed-but-unconfirmed path stays open
(keep going, or ask the confirmation prompt); a confirmation with no observed
signal is rejected, because there is nothing to record.

Bound a session that never converges with a backstop: a per-session action
budget AND a wall-clock limit. When either is exhausted before both halves hold,
stop LOUDLY as not converged: write NO goal and tell the human plainly what you
reached and what was missing, so the run is visible and re-runnable rather than a
silent empty file. Never pretend a half-taught goal is believed.

## Write the goal (only after the human confirms)

The output is OPERATIONAL knowledge: what counts as success, what is risky, what
is unknown. It is NEVER a click-by-click recording of the path you took. Once the
dual end condition holds:

1. Write a goal YAML to a staging file that records what you OBSERVED:
   - `success_signals`: the signals you actually saw, at least one behavioral
     and one network of different types (for example "a Sign out control is
     present" plus "POST to the session endpoint returns 2xx and sets a session
     cookie"). Each carries `source_type = human` with the confirming human as
     its `source_id` (this is the human seed) and a `confidence`.
     Pick a `type` per signal that a later regress run can REPRODUCE in that same
     type (ADR-0028): a regress agent driving the browser will be asked to
     confirm each signal IN its declared type, so do NOT type a fact `network`
     unless a run can actually observe it at the network level. If the only way
     to confirm a fact in a later run is by looking at the page, seed it
     `behavioral` / `text` / `url`, not `network`. A type no regress agent can
     reproduce makes a genuinely-passing goal come back inconclusive (and then a
     false regression). This constrains WHICH types you choose; it does not relax
     the rule above that a believed oracle needs at least two DIFFERENT types
     (ADR-0005).
   - `auth_state`: `authenticated` plus the abstract `scope` from the role
     prompt. Never the credential or the cookie value.
   - optional `failure_signals`, `risks` (with a STRUCTURED trigger, never free
     text), and `uncertainties`.
2. Validate and install it with the CLI:
   `praxis learn <goal_id> --from-file <staging-file>`. This validates against
   the schema and REJECTS a non-human oracle, so a machine-graded oracle cannot
   masquerade as a seed. If it rejects, fix the file and re-run.
3. Show the human the installed file under `.praxis/knowledge/` and ask them to
   review and commit it. You NEVER commit on their behalf: the seed lands only
   when the human commits it.

## Structured checks for relational and after-action facts

Some success/failure facts are NOT a fixed phrase and cannot be written as prose
a later run matches by wording. They are RELATIONS or AFTER-ACTION states:

- a COUNT DELTA: "the list comes back with exactly one fewer row", "the cart
  total goes up by one".
- an AFTER-ACTION ABSENCE/PRESENCE: "the archived id is no longer in the list",
  "the new row is now present".

For these, author a typed `check` on the signal instead of prose, the SAME way a
risk carries a STRUCTURED `trigger` rather than free text. A prose signal for a
relation cannot be confirmed by a later run (the wording varies every run), so it
comes back inconclusive and then a FALSE regression. The `check` is evaluated by
the runner over raw data the regress agent reports, so it matches the FACT, not
the phrasing.

There are exactly two check kinds (keep to these; do not invent others):

- `list_count_delta` with `expect_delta: <signed int>` (e.g. `-1` for "one
  fewer"). The regress agent reports the BEFORE and AFTER counts it saw; the
  runner checks `after - before == expect_delta`.
- `element_membership` with `identifier_slot: <name>` and `expect: present` or
  `expect: absent`. The slot names the per-run id to track (an abstract name like
  `campaign_id`, NEVER a concrete number). The regress agent reports the concrete
  id it saw and whether it is present after the action; the runner checks the
  membership equals `expect`.

When you teach a goal whose success involves a relation, you must still OBSERVE
both sides live (count the list before and after the action; note the id
disappear) and have the human CONFIRM it, exactly as for any seeded signal. Then
write the check. Split a compound prose fact into one signal per check.

Concrete shape (the archive/delete case, two structured signals replacing one
prose sentence):

    success_signals:
    - type: network
      value: a fresh list load after archiving returns one fewer campaign
      check:
        kind: list_count_delta
        expect_delta: -1
      provenance: { source_type: human, source_id: <confirming human>, ... }
      confidence: 0.9
      status: believed
    - type: network
      value: the archived campaign id is no longer in the list
      check:
        kind: element_membership
        identifier_slot: campaign_id
        expect: absent
      provenance: { source_type: human, source_id: <confirming human>, ... }
      confidence: 0.9
      status: believed

`value` stays a plain human-readable sentence (it is the description and the
grouping key); the invariant the runner enforces lives in the typed `check`
fields. The concrete per-run id and counts are NEVER written into knowledge: the
seed holds only the abstract `check`, the run reports the concrete numbers as a
redacted per-run observation. `praxis learn` validates the check against the
schema and rejects a malformed one (an unknown kind, a non-integer delta, an
empty slot), the same loud write-time rejection a free-text risk trigger gets.

A fact that simply IS a stable phrase (a route, a banner) does not need a check;
prose, or an ADR-0030 inline `{slot}` in the value, is enough. Reach for a
`check` only when the fact is a relation or an after-action membership.

## Do not silently overwrite a believed goal

Before authoring, check whether the goal already exists in
`.praxis/knowledge/<goal_id>.knowledge.yaml` with a believed success signal. If
it does, do NOT overwrite it. Instead emit a CONTESTED candidate refinement
under `.praxis/candidates/<goal>/` proposing the change, and tell the human
plainly: the believed goal was preserved, your re-teach landed as a contested
candidate for review. The trusted oracle is never quietly replaced by one fresh
session's view.

## Protocol, end to end

1. Confirm you are in a project with a `.praxis/` tree (run `praxis init` first
   if not). Get the plain-language intent and a stable `goal_id` from the human.
2. If the goal already exists believed, switch to the re-teach path (a contested
   candidate, not a new seed) and say so up front.
3. Open the app in the browser and work toward the happy path. At the login,
   read the credentials from `.praxis.secrets` and type them in; for 2FA, ask the
   human for the code and type it. When you cannot find a control, ask a
   navigation-hint prompt. When you need the scope, ask a role prompt. One typed
   question at a time.
4. After a successful login, save the session (the section above) so future runs
   skip the 2FA.
5. When you believe you observed the happy path, ask a confirmation prompt. Only
   an affirmative reply meets the human-confirm half. Both halves met is the dual
   end condition.
6. Write the goal YAML and `praxis learn` it. Show the human the file to review
   and commit. If you did not converge, surface the loud not-converged outcome
   instead.
7. Report honestly: name the file, remind the human the seed lands only when they
   commit it, and never claim a goal is believed before that.
