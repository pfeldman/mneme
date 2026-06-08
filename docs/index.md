# Praxis

Praxis is a shared layer of operational knowledge for the QA agents that test your app.

It does not record click-by-click test scripts. It stores what your team has learned
about how the app actually behaves: what counts as success for a goal, what counts as
failure, where the risky corners are, and what is still unknown. That knowledge lives
in your git repo and travels with `git pull` and `git push`, like any other code your
team shares.

```
pip install praxis-qa
```

Repository: [github.com/pfeldman/mneme](https://github.com/pfeldman/mneme)

There is nothing to sign up for. There is no hosted account, no dashboard, and no
service to log into. You install a library, you commit a `.praxis/` folder, and your
knowledge is shared the same way your code is.

## What "operational knowledge" means

A recorded test script says "click the button at the top right, then fill the form".
The moment the app changes that button, the script breaks, and a person has to go fix
the recording. The script never understood what the test was for; it only remembered
where things were last time.

Operational knowledge is the opposite. For the goal "a user can log in", Praxis stores
things like:

- a "Sign out" control becomes available after a successful login (a behavioral fact)
- the login request returns a success response and sets a session cookie (a network fact)
- a risk: too many failed attempts can trigger a lockout
- an open question: we are not sure what happens on a brand-new device

None of that is a click path. It is a description of how you tell whether the app did
the right thing. An agent reads this knowledge, figures out the clicks on its own each
time, and checks the live app against what the knowledge says. When the button moves,
the knowledge still holds; only the disposable click path changes.

## The bet behind Praxis

Praxis is built on a bet, not a settled fact. The bet (sourced to
[ADR-0009](adr/0009-phase-1-scope-and-praxis-reframe.md)) is this: operational
knowledge about a specific app outlives a cache of recorded procedures, because the
procedure rots on every UI change while the knowledge of "what success means here" stays
true across redesigns. A stranger to your app cannot reconstruct that knowledge from the
public surface; your team accumulates it by actually using the app.

We are honest that this is the project's bet and that the harder head-to-head proof (a
direct comparison against a procedural self-healing cache) is deferred work, not a result
we can show you yet. The one quantitative result we do have is below, with its limits
stated plainly.

## Sharing knowledge through git

Praxis has no backend. The shared memory is your git repo
([ADR-0021](adr/0021-praxis-directory-convention.md)).

- A teammate's discovery reaches you on `git pull`.
- Your discovery reaches the team on `git push`.
- Who can change shared knowledge is decided by who can push to the repo, the same git
  permissions your code already uses.
- The history of every change is `git log` over the `.praxis/` folder, so you can see who
  taught what and when. That git history is the audit trail; there is no separate
  dashboard to maintain.

Knowledge the team trusts lives under `.praxis/knowledge/`. Things an agent proposed but
nobody has vetted live under `.praxis/candidates/`. A proposal becomes trusted knowledge
only when a human merges it, which is an ordinary git merge a person reviews, never an
automatic promotion.

## How it feels to use

Praxis has three operations, all in plain language.

### Teach: author knowledge by showing, not by writing YAML

`teach` ([ADR-0022](adr/0022-praxis-teach-skill.md)) is how you create knowledge for a
new goal. You give it a goal in natural language, like "a user can log in". An agent
explores the live app, and when it gets stuck it asks you a focused question (where is
the login control, what role should this be, is this the success screen you meant). When
it reaches what it thinks is success, it asks you to confirm. You confirming is what
makes the knowledge trustworthy: a person vouched for it, the agent did not certify
itself.

Any credential you type during a teach session is used to drive the browser and then
discarded. It is never written into the knowledge, never committed, never logged.

### Regress: check the app against what you know, before you ship

`regress` ([ADR-0023](adr/0023-regress-explore-dual-surface-and-report.md)) re-checks
every goal you have taught against the live app and gives you one report. For each goal,
you get one of three plain verdicts:

- **OK**: the app still behaves the way your knowledge says. Nothing to do.
- **REGRESSED**: the app broke. A success signal that should be there is gone, or a
  failure you know about showed up. This is a real bug. File it.
- **STALE**: the app changed on purpose, and now your stored knowledge is out of date.
  Nothing is broken; the knowledge needs updating to match the new app.

This break-vs-drift distinction is the whole point. A plain test that goes red tells you
something is wrong but not whether the app broke or whether you just changed the app on
purpose. Praxis tells you which, and routes you to the right action: REGRESSED means file
a bug against the app, STALE means update the knowledge. A REGRESSED verdict is loud: it
fails the run and names the goal and the exact signal that flipped, so a real regression
can never hide behind a "mostly green" summary.

### Explore: hunt for the risky corners

`explore` ([ADR-0023](adr/0023-regress-explore-dual-surface-and-report.md)) goes off the
happy path on purpose, probing the risks and open questions in your knowledge to look for
trouble a normal test would not. Anything it finds is written down as a proposal under
`.praxis/candidates/`, never promoted on its own. A person reviews and merges what is
worth keeping.

## The one number we can show you

In our Phase 1 experiment
([ADR-0010](adr/0010-phase-1-regression-recall-verdict.md)), an agent equipped with
Praxis knowledge caught more planted regressions than a strong baseline agent that was
given the same goals plus the app's public README. On the `phase-1-r1` release the
knowledge-equipped agent's recall was higher overall and notably higher on the categories
a stranger to the app would not think to probe, and it did so without raising more false
alarms.

This is the only quantitative claim on this site, and it is provisional on purpose:

- It is one experiment, on one app under test, at a small sample size.
- It ran against a single model, so cross-model generalization is not yet shown.
- It is not a benchmark, and it is not a head-to-head against a procedural self-healing
  cache; that comparison is deferred (Phase 1.5).

We state it this way deliberately. Praxis exists because bad knowledge that looks fine is
the dangerous failure, so the project refuses to do to its own claims what it warns you
against doing to your app: overstate them quietly. Every claim here is traceable to a
decision record or it is not made.

## Get started

```
pip install praxis-qa
```

Then read the worked examples:

- [testapp](examples/testapp.md): the smallest end-to-end teach-then-regress loop.
- [Conduit](examples/conduit.md): the same loop on a realistic public web app.
- [Gitea](examples/gitea.md): Praxis pointed at a real open-source app you did not build.

Repository and source: [github.com/pfeldman/mneme](https://github.com/pfeldman/mneme).
