# Example: Conduit (a realistic web app)

[Conduit](https://github.com/gothinkster/realworld) is the RealWorld demo app: a
Medium-style blogging site (sign up, log in, write and publish articles, favorite, follow,
comment) that exists as a reference implementation of a real-world single-page app. It is
the app the project picked as its realistic system under test
([ADR-0016](../adr/0016-real-app-sut-selection.md)), and it runs locally with a single
`docker compose up`.

This example shows the same teach-then-regress loop as the [testapp](testapp.md) example,
but against a non-toy app with real authentication, a real database, and multi-step flows.
The point is to show that the knowledge layer is not tied to the toy app: the same
operations work on a realistic SPA. It is a demonstration, not a benchmark.

## Set up

Bring Conduit up locally (its repo documents the `docker compose` path), then point Praxis
at it:

```
pip install praxis-qa
praxis init
```

## Teach a couple of goals

Conduit has several goal-shaped flows. Two natural first goals:

```
/praxis:teach a registered user can log in
/praxis:teach a logged-in user can publish an article
```

For the login goal, teach explores the app, asks you for the login credential when it hits
the auth wall, and confirms the logged-in state with you. The knowledge it writes is
operational, not a click path: for example, a success signal that the user's profile menu
(with a sign-out option) becomes available, and a network signal that the login request
returns a success response carrying a token. As always, the credential is discarded after
it drives the browser; only the abstract authentication posture is stored.

For the publish goal, the success knowledge captures the post-condition, not the steps to
get there: the new article appears in the global feed, and the create-article request
returns a success response with the new article's slug. That is a checkable fact about
what success means, independent of where the editor's buttons happen to be today.

You review each emitted goal file and commit it under `.praxis/knowledge/`.

## Regress

```
praxis regress
```

With Conduit healthy, both goals come back **OK**. If a code change broke login (say the
login request started failing), regress would return **REGRESSED** for the login goal,
name the signal that flipped, and exit non-zero so a CI gate fails. If the team
intentionally changed the publish flow such that the stored signal no longer matched but
publishing still worked, that goal would come back **STALE**, routing you to re-teach the
knowledge rather than file a bug.

## Explore

Conduit has flows with interesting corners, like whether favoriting an article twice
double-counts, or whether a user can reach an owner-only action on someone else's article.
`praxis explore` probes the risks and open questions in your knowledge and writes any
findings as contested candidate files under `.praxis/candidates/`. A person reviews and
merges what is worth keeping; nothing is promoted on its own.

## What this example shows

That the teach / regress / explore loop runs unchanged on a realistic, database-backed SPA
with real auth, not just the toy app. It is a demonstration that the knowledge layer
generalizes off `testapp`, not a performance measurement. For the single quantitative
result the project has, see the [landing page](../index.md).
