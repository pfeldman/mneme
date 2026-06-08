# Example: testapp (the minimal loop)

This is the smallest possible walkthrough: teach one goal, then regress it, end to end,
against a tiny fully-controlled app. It exists so you can see the whole teach-then-regress
loop with no moving parts. It is a demonstration, not a benchmark.

The app under test is `testapp`, the toy app the project uses for its own experiments
(`experiments/ui-mutation/testapp.py`). It is a few-hundred-line web app with a login
form, a search page, and a checkout flow, plus deliberately plantable bugs. Because it is
fully controlled, the loop is easy to follow without any real-world noise.

## Step 1: set up the knowledge folder

```
pip install praxis-qa
praxis init
```

`praxis init` creates the `.praxis/` folder in your repo: an empty `knowledge/` for the
goals you trust, an empty `candidates/` for proposals, and the git ignore lines that keep
raw run logs and your secrets out of version control. It commits nothing on its own; you
commit the `.praxis/` tree like any other code.

## Step 2: teach the login goal

You start with a goal in plain language and let Praxis figure out the app:

```
/praxis:teach a user can log in
```

An agent opens the app and walks toward logging in. When it needs the login credential it
asks you for it (or reads it from your local secrets file). When it reaches what looks like
a logged-in state, it asks you to confirm that this is the success you meant. You confirming
is what makes the result trustworthy knowledge rather than a guess the agent made about
itself.

The knowledge it writes describes the goal, not the clicks. For the login goal it captures
two independent success signals of different kinds:

- behavioral: a "Sign out" control is present on the page after login
- network: the login request returns a success response and sets a session cookie

It also records the abstract authentication posture (authenticated, role "user"). It does
NOT record the email or password you typed. The credential drove the browser and was then
discarded; it never enters the knowledge file. The emitted goal looks like this:

```yaml
schema_version: '0'
goal_id: login
goal: a user can log in
target:
  app: testapp
  environment: local
  observed_app_versions:
    - local
success_signals:
  - type: behavioral
    value: a Sign out control is present on the post-login page
    provenance:
      source_type: human
      source_id: you
      observed_app_version: local
      observation_count: 1
    confidence: 1.0
    status: believed
  - type: network
    value: POST /session returns 2xx and sets a session cookie
    provenance:
      source_type: human
      source_id: you
      observation_count: 1
    confidence: 1.0
    status: believed
auth_state:
  authenticated: true
  scope: user
```

You review that file and commit it under `.praxis/knowledge/login.knowledge.yaml`. That
commit is your shared knowledge for the login goal.

## Step 3: regress against the live app

Now you can check the app against what you taught:

```
praxis regress
```

With the app behaving normally, the login goal comes back **OK**: the "Sign out" control is
there and the login request still succeeds, so the app behaves the way the knowledge says.

Now plant a regression. `testapp` can be told to make the login request fail. Run the gate
again and the verdict flips to **REGRESSED**, naming the goal and the exact signal that
went wrong (the login request no longer succeeds). The run exits non-zero, which is what a
CI gate keys on. REGRESSED routes you to "file a bug against the app".

If instead the app had changed login on purpose, in a way where login still works but the
stored signal no longer matches, the verdict would be **STALE**: nothing is broken, the
knowledge is just out of date and should be re-taught. STALE routes you to "update the
knowledge", and updating it is a human re-teach, never an automatic edit.

That is the whole loop: teach a goal once with a human confirm, then regress as often as
you like and get a plain OK / REGRESSED / STALE verdict that tells you whether the app
broke or the knowledge drifted.

A signal whose value carries a per-run instance (an id, a hostname, a generated name) can
optionally be taught as a CHECKABLE FACT with a `value_predicate` (ADR-0030): the durable
text is matched exactly and a `{slot}` marks the per-run token the matcher tolerates on
presence (or `{slot:numeric}` / `{slot:uuid}` shape) only. For example a network signal
`value_predicate: POST /session returns 2xx and sets cookie {session_cookie}` matches an
honest run that reports the real cookie value, while a `returns 500` run does NOT. This is
stricter than the default word-overlap match, never looser; see
[03 - Knowledge schema](../03-knowledge-schema.md).

## What this example shows and what it does not

It shows the mechanics of the loop end to end on a controlled app: how teach produces
human-seeded knowledge, how regress reads it back against the live app, and how the
break-vs-drift verdict routes you to the right action. It is not a measurement of how well
Praxis performs; for the one quantitative result the project has, see the
[landing page](../index.md). The realistic examples are [Conduit](conduit.md) and
[Gitea](gitea.md).
