# Example: a login with two-factor (the saved session)

Some real apps do not let you in with just a password. After the password they ask
for a second one-time code: a code from an authenticator app, or a code emailed to
your inbox. That second factor is good security, and it is also a problem for any
automated check: if every run had to pass two-factor by hand, you could not run
`praxis regress` on a schedule or in CI at all.

Praxis handles this by saving the session, not the password. This page explains, in
plain terms, how that works and what to do when the saved session stops working. All
of it is sourced to [ADR-0026](../adr/0026-persistent-auth-session-reuse.md).

## You pass two-factor once, not every run

When you teach a goal on an app that needs two-factor, you log in for real one time:
you type the password, you pass the second factor (you read the emailed code or copy
it from your authenticator app), and the app lets you in. At that moment the browser
holds a proof that it is logged in. Praxis SAVES that proof, the authenticated
session, and REUSES it on later runs.

So the next time `praxis regress` or `praxis explore` runs, it does not log in again.
It hands the saved session to the browser, which is already logged in, and goes
straight to checking the goal. You passed two-factor once, when you taught the goal,
not on every run after it. That one human login is also what makes the saved session
trustworthy: a person logged in for real, exactly the way teach itself needs a person
to confirm what success means.

## The saved session is a secret, like the password

The saved session is the live proof of a logged-in browser. Anyone who has it is
logged in as that account, no password and no second factor needed. So it is a secret
of the same kind as the password, and Praxis treats it exactly like one:

- locally it lives in a file that git ignores, never committed, the same way your
  login credential lives outside committed knowledge
- in CI it is a runner secret in your CI's secret store, supplied to the run from
  there, never pushed to the repo
- it is never written into your knowledge, never logged, never echoed

Knowledge only ever records the abstract fact that a goal runs "authenticated, as
this role". The session itself, its cookies, and any two-factor code never cross into
a knowledge file. The session is an auth artifact kept beside your knowledge, never
inside it.

One saved session covers a role, not a single goal. Every goal you test as the same
user reuses the one saved session for that user, so the number of saved sessions
tracks the roles you test, not the goals.

## When the session expires: AUTH-EXPIRED

Saved sessions do not last forever. Apps expire them after a while, the same way you
get logged out of a site you have not visited in a long time. When a run finds the
saved session has expired (the app bounces it back to the login page, so the things
the knowledge expects to see are not there), Praxis does NOT call that a bug and does
NOT call it green. It reports a distinct third outcome: **AUTH-EXPIRED**.

AUTH-EXPIRED sits alongside the [regress](ci.md) verdicts and means something specific
and different from both of them:

- **REGRESSED** means the app broke. File a bug.
- **STALE** means the app changed on purpose and your knowledge is now out of date.
  Update the knowledge.
- **AUTH-EXPIRED** means the run could not log in: the saved session expired. The app
  is fine and your knowledge is fine. Re-authenticate, that is, refresh the saved
  session.

Keeping these three apart is the whole point. If an expired session were reported as
REGRESSED, you would chase a bug that is not there. If it were reported as a green OK,
the run would have skipped the check entirely and hidden it. AUTH-EXPIRED is loud and
correctly named: it tells you the one thing to do, refresh the session, and nothing
was silently skipped.

What happens on an AUTH-EXPIRED run depends on where it ran:

- With a person present (a local teach or skill session), the run asks you to log in
  again. You pass two-factor once more, and the refreshed session is saved back.
- With no person present (the console or CI), the run fails loudly, names AUTH-EXPIRED
  and the role whose session expired, and exits non-zero. It never quietly passes and
  never cries "regression". A person then refreshes the stored session.

## Refresh cost: emailed code vs authenticator app

How often a human has to step in depends on which kind of second factor the app uses.

- An **emailed one-time code** changes on every login and has to be read from an
  inbox, which no automated runner can do. So when the saved session expires, a person
  has to re-login locally, pass the emailed code, and update the CI secret with the
  fresh session. CI keeps using the stored session until it expires, then surfaces
  AUTH-EXPIRED until a person refreshes it. This is a periodic human chore, set by how
  long the app keeps a session alive, not by Praxis.
- An **authenticator-app code (TOTP)** is generated from a fixed shared seed. That seed
  is a secret you can store, so a runner can generate the current code from it and log
  in on its own. With this kind of second factor, CI can refresh its own session, with
  no human step.

Praxis does not require your test account to use one or the other. It records the cost
honestly: an emailed code carries a recurring manual-refresh chore, an authenticator app
removes it, so you can choose with the trade-off in plain sight. Either way, use a
dedicated low-privilege test account: the saved session is a real credential and should
be guarded like one.

## How this connects to CI

In [CI](ci.md) the saved session is one more runner secret, sitting next to the API key
and the app login credential. The example workflow shows it as `PRAXIS_AUTH_STATE_USER`,
a secret a person refreshes when it expires. An AUTH-EXPIRED run in CI is the signal that
a person has to refresh that secret.
