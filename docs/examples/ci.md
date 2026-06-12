# Running Praxis in CI

This page shows one way to run `praxis regress` as a required check in your CI, and,
optionally, to run `praxis explore` on a schedule. The workflow below is an EXAMPLE you
copy and adapt. Praxis ships no reusable CI action.

## CI is your CI

Praxis does not publish a GitHub Action, a plugin, or any turnkey CI product
([ADR-0024](../adr/0024-ci-integration-invoking-commands.md)). Integration is exactly
this: your CI calls the `praxis` console commands, and gates on the exit code. There is
nothing between `praxis regress` and your pipeline except your own workflow file.

That means a few things stay yours, not Praxis's:

- **The push, the pull request, and the runner auth are standard git you own.** Praxis
  never pushes, never opens a pull request, and never authenticates your runner.
- **The secrets are your runner secrets.** The API key for the CI brain and the app login
  credential live in your CI's secret store and are read from the runner environment. They
  are never committed to the repo and never echoed into the logs.
- **Promotion is a human git merge.** When exploration discovers something, it writes a
  contested candidate file. Turning that into trusted knowledge is a person reviewing and
  merging it, never an auto-merge and never an auto-promotion by a count of CI runs.

The only contract Praxis provides is the exit code: a REGRESSED verdict exits non-zero, so
any CI that can run a process and read an exit code can gate on it.

## The regress gate

`praxis regress` runs every believed goal under `.praxis/knowledge/` and fails the job on
its loud non-zero exit, which a REGRESSED verdict produces
([ADR-0023](../adr/0023-regress-explore-dual-surface-and-report.md)). One REGRESSED goal
fails the whole run and names the goal and the signal that flipped, so a real regression
cannot pass behind a "mostly green" summary. Wire it on pull requests and on release tags
and it becomes a required check with no Praxis-specific machinery.

A STALE verdict (the app changed on purpose, the knowledge is now out of date) is a
knowledge-update task, not a code bug. How you treat STALE is your CI policy: hard-fail on
it, or read the report and open a follow-up to re-teach the goal. That choice is yours,
not a Praxis behavior.

!!! warning "A STALE-only run exits 0; a gate that reads ONLY the exit code passes drifted knowledge silently"
    By design, STALE does NOT fail the run: `praxis regress` exits `0` when every
    non-OK goal is STALE (no REGRESSED, no ERROR, no AUTH-EXPIRED). That is correct
    for the verdict (the app changed on purpose, the fix is a human re-seed, not a
    red gate), but it has a sharp edge for CI: a job that gates on **the exit code
    alone** will go green while your knowledge is drifting out of date, and the
    drift accumulates invisibly until a real REGRESSED finally trips the gate on top
    of knowledge no one trusts anymore.

    If you care about drift (you should), do NOT gate on the exit code alone. Read
    the report and act on STALE: surface the `STALE` count from the aggregate
    markdown or the `<skipped>` count from the JUnit XML
    (`.praxis/runs/<timestamp>/regress-aggregate.{md,xml}`), fail or warn on
    `skipped > 0`, or open a re-teach follow-up. The exit code only tells you the
    app did not break; it does NOT tell you the knowledge is still accurate.

## The CI brain and the credentials

When the commands run autonomously in CI there is no human session to borrow, but the brain
is the SAME one the local console runner uses: `praxis regress` / `praxis explore` shell out
to the Claude Code CLI headless (`claude -p`,
[ADR-0027](../adr/0027-console-test-runner-and-claude-p-brain.md)). Nothing in the installed
wheel imports the `anthropic` SDK, so the `[live]` pip extra is NOT what drives a CI run; it
exists only for the offline experiment harness. What a runner needs is Praxis plus the
`claude` binary on PATH:

```
pip install praxis-qa
npm install -g @anthropic-ai/claude-code
```

The brain credential (an `ANTHROPIC_API_KEY` for the autonomous, no-human CI case, the way
the Claude Code CLI authenticates), and any app login credential a run needs, are supplied
as runner secrets read from the environment (the
[ADR-0021](../adr/0021-praxis-directory-convention.md) secrets channel). An environment
variable wins over any `.praxis.secrets` file, so in CI you pass them purely as secrets with
no file on disk. Praxis reads them at runtime and never writes them into the repo or the
logs.

If the app's login needs two-factor, the run also reads a saved authenticated session as a
runner secret (`PRAXIS_AUTH_STATE_USER` in the example,
[ADR-0026](../adr/0026-persistent-auth-session-reuse.md)). A human passes two-factor once
locally and saves the session, and CI reuses it so it never has to pass two-factor itself.
That session is a secret, never committed and never echoed. When it expires, a run reports
the distinct **AUTH-EXPIRED** outcome (not REGRESSED, not green) and a human refreshes the
secret. See [Login with two-factor](auth-and-2fa.md) for the full walkthrough.

## Pinning the brain model

Which model drives the brain affects verdict QUALITY, not just speed. A cheaper or
faster model is a real false-alarm risk for the regress brain: it can mis-navigate the
app (open the wrong widget, never reach the flow under test) and then honestly report
the expected signals absent, which surfaces as REGRESSED on a healthy app. The risk is
on the navigation/grounding side, so no evidence rule downstream can catch it. It also
means two operators (or your laptop and CI) regressing the same committed knowledge
with different claude CLI defaults can reach different verdicts.

Pin the model deliberately, per project
([ADR-0034](../adr/0034-brain-model-pin-and-precedence.md)):

- **`brain_model` in `.praxis/config.yaml`** is the committed pin the whole team and CI
  share (`praxis init` scaffolds it commented out).
- **`PRAXIS_BRAIN_MODEL`** as a runner environment variable overrides the committed pin
  for a pipeline, mirroring the env-over-file precedence of the secrets channel. It is
  not a secret; a plain `env:` entry is fine.
- **`--model`** on `praxis regress` / `praxis explore` overrides both for one run, which
  is how you A/B a cheaper candidate pin.

Unset everywhere means whatever the claude CLI defaults to (exactly the previous
behavior). The value is passed to `claude -p --model` verbatim; an unknown model errors
loudly and fails the run, never silently. Before trusting a cheaper pin, validate it
with a few runs against a known-good app state: if it false-alarms there, it will
false-alarm in your gate.

## Multiple environments: one job matrix over PRAXIS_ENV

If the project declares an `environments` map in `.praxis/config.yaml`
([ADR-0035](../adr/0035-multi-environment-support.md); the full setup is walked through in
[One app, two deployments](multi-env.md)), a CI run selects its deployment with the
`PRAXIS_ENV` variable. That is the entire multi-env CI surface: Praxis ships no matrix
machinery (ADR-0024 stands), so a multi-env pipeline is your own job matrix over that one
variable.

```yaml
jobs:
  regress:
    strategy:
      fail-fast: false
      matrix:
        praxis_env: [dev2, prod]
    runs-on: ubuntu-latest
    steps:
      # ... checkout, install Praxis and the Claude Code CLI exactly as above ...
      - name: praxis regress (the gate, one environment per leg)
        env:
          PRAXIS_ENV: ${{ matrix.praxis_env }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          # Saved sessions are PER ENVIRONMENT (ADR-0035): the variable name a
          # run reads encodes the environment (PRAXIS_AUTH_STATE_<ENV>_<ROLE>),
          # so name the runner secrets the same way, export every environment's
          # secret on every leg, and each run reads only the one for its
          # selected environment. There is no fallback to the unscoped
          # PRAXIS_AUTH_STATE_<ROLE> name on a declared project.
          PRAXIS_AUTH_STATE_DEV2_USER: ${{ secrets.PRAXIS_AUTH_STATE_DEV2_USER }}
          PRAXIS_AUTH_STATE_PROD_USER: ${{ secrets.PRAXIS_AUTH_STATE_PROD_USER }}
        run: praxis regress
```

Each leg gates independently: one run checks one environment, and the exit-code contract
is unchanged per run, so a dev2 regression fails the dev2 leg no matter how green prod is
(and vice versa; the two deployments' evidence is never folded together). The JUnit suite
name carries the environment (`praxis-regress[dev2]`, `praxis-regress[prod]`), so the
legs stay distinguishable in your CI's test UI, and the STALE warning above applies per
leg: read each leg's report, not just its exit code.

The session secret is one per role PER ENVIRONMENT: a human seeds
`PRAXIS_AUTH_STATE_DEV2_USER` by logging in against dev2 once and
`PRAXIS_AUTH_STATE_PROD_USER` against prod, because a saved session is domain-bound and
Praxis deliberately never borrows one environment's session for another. An app login
credential that differs per deployment can be scoped the GitHub-native way (a GitHub
`environment:` per matrix leg with its own secrets) or selected per leg in the workflow
expression; a credential both deployments share is just the one secret, as before.

Exporting `PRAXIS_ENV` pipeline-wide is safe for repos that have not adopted
environments: on a project with no declared `environments` map the variable is ignored
with a one-line stderr notice, and the run behaves exactly as today.

## Optional: scheduled exploration

If you want autonomous exploration, run `praxis explore` on a schedule. It hunts off the
happy path and writes any findings as contested candidate files under
`.praxis/candidates/`, one file per observation. Then it exits. That is where Praxis
stops.

What happens next is your CI and your git: committing the files, pushing them, and opening
a pull request for review are steps you own. Never force-push `.praxis/`, never auto-merge
a candidate, and never auto-promote one into trusted knowledge. A human merge is the
promotion.

`teach` never runs in CI. Authoring new goals is a local, human-in-the-loop session whose
output a human commits and pushes; an autonomous CI teach would certify its own oracle,
which Praxis forbids.

## The example workflow

Copy this into `.github/workflows/` in your own repo and change it freely. It is a
starting point, not a supported action.

```yaml
--8<-- "examples/ci/praxis-regress.yml"
```
