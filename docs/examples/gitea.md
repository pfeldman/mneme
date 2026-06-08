# Example: Gitea (a real open-source app you did not build)

[Gitea](https://about.gitea.com/) is a widely used open-source, self-hosted git service:
repositories, issues, pull requests, the kind of app many teams already run. It is a real,
externally recognizable application that the project did not author, and it offers a public
demo at [try.gitea.io](https://try.gitea.io). That makes it a good third example: you can
see Praxis pointed at something built by other people, with a public surface a stranger
could reach.

This is the most honest framing point of the three: it is a demonstration of the loop
against a real app, NOT a benchmark and NOT a claim about Gitea or about how Praxis scores
on it. There are no numbers here. The single quantitative result the project has lives on
the [landing page](../index.md) and is from a different, controlled experiment.

## Set up

Use a Gitea you control (the official Docker image stands one up quickly) or the public
[try.gitea.io](https://try.gitea.io) demo. Then:

```
pip install praxis-qa
praxis init
```

The public demo resets periodically, so treat anything you create there as throwaway. For
anything beyond a quick look, run your own Gitea instance.

## Teach a happy path

A clear happy path on Gitea is: log in, then create something (a new repository, or an
issue on an existing repository). Two natural goals:

```
/praxis:teach a user can sign in
/praxis:teach a signed-in user can create a new repository
```

For sign-in, teach drives the login, asks you for the credential when it reaches the auth
wall, and confirms the signed-in state with you. The knowledge captures operational facts,
for example a behavioral signal that the user's avatar menu (with a sign-out option) is
present after login, and a network signal that the sign-in request succeeds and
establishes a session. The credential you type is discarded after it drives the browser;
only the abstract authentication posture (authenticated, role "user") is stored.

For creating a repository, the success knowledge captures the post-condition: the new
repository's page is reachable afterward, and the create request returns a success
response. That is a fact about what success means, not a recording of which buttons you
clicked to get there.

You review each emitted goal file and commit it under `.praxis/knowledge/`.

## Regress

```
praxis regress
```

Against a healthy Gitea, the goals come back **OK**: the live app behaves the way your
knowledge says. If sign-in broke, regress would return **REGRESSED**, name the signal that
flipped, and exit non-zero. If Gitea changed a flow on purpose so the stored signal no
longer matched but the goal still worked, the verdict would be **STALE**, telling you to
re-teach the knowledge rather than file a bug. Same plain break-vs-drift verdict as the
other examples, now against an app you did not build.

## What this example shows and what it does not

It shows that the teach-then-regress loop runs against a real, public, third-party
application, so the knowledge layer is not tied to apps the project authored. It does NOT
claim a result on Gitea, does not benchmark Praxis against anything, and reports no
numbers. It is a demonstration of the loop on a recognizable app, nothing more. For the one
quantitative claim the project stands behind, with its limits stated, see the
[landing page](../index.md).
