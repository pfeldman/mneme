# Example: one app, two deployments (multi-environment)

A common real-world shape: the same product deployed twice, a dev/staging instance
(call it dev2) and prod. Same goals, same product knowledge, different base URL and
different credentials per deployment. Without multi-environment support that forces two
sibling `.praxis/` trees with every goal YAML duplicated by hand, and duplicated
knowledge drifts: two copies of one product's operational knowledge, edited
independently, disagreeing silently.

[ADR-0035](../adr/0035-multi-environment-support.md) fixes this: ONE committed knowledge
tree runs against N deployments. A goal is a capability of the product; which deployment
a run checks it on is an operational input, selected per run. This page walks the
dev2+prod setup end to end.

## Declare the environments

On a new project, declare every deployment at init:

```
praxis init --app myapp \
  --environment dev2=https://dev2.example.com \
  --environment prod=https://example.com \
  --default-env dev2
```

`--environment NAME=URL` is repeatable; `--default-env` names the one teammates get when
they do not pick. (These flags are mutually exclusive with the legacy single-env
`--env` / `--base-url` init pair, which keeps working for single-deployment projects.)

The scaffolded `.praxis/config.yaml` carries the committed map:

```yaml
environments:
  dev2:
    base_url: https://dev2.example.com
    # observed_app_version: 2.6.0   # optional per-environment decay anchor
  prod:
    base_url: https://example.com
default_env: dev2
```

Everything under `.praxis/knowledge/` stays product-level and environment-free: goals,
signals, risks, `auth_state`. The environment name is stamped onto run records at run
time, never onto knowledge.

A project whose config.yaml has NO `environments` key behaves exactly as before: no
flag, no new files, same paths, same env-var names, same prompts, same exit codes.

## How a run picks its environment

Selection mirrors the brain-model pin precedence (flag > env var > committed config):

1. `--env <name>` on `praxis regress` / `praxis explore` / `praxis status`
2. the `PRAXIS_ENV` environment variable (the CI channel)
3. `default_env` in `.praxis/config.yaml`
4. a single-entry `environments` map auto-selects its only entry

Resolution failures are loud: an unknown name (from the flag or the variable) errors
naming the declared environments, and a declared multi-entry map that resolves to
nothing errors naming the three ways to pick one. The resolved environment and its
winning source are printed once per run on stderr, so a report is never ambiguous about
which deployment it describes. Two edges: `--env` on a project with no declared map is
an error (you asked for something the project does not have), while `PRAXIS_ENV` on such
a project is ignored with a one-line notice, so a pipeline-wide export cannot break
repos that have not adopted environments.

## The agent learns the URL at run time

The selected environment's `base_url` reaches the agent as an explicit prompt line:

```
App under test: https://dev2.example.com
```

so goal text and signal values can say "the app under test" instead of hardcoding a
host. When you teach goals on a multi-env project, keep them deployment-agnostic: say
"the app under test", never a host, and express `url`-type signals as paths
(`/settings/billing`), never absolute URLs. The environment name itself never belongs
inside a signal value.

Existing goals that embed a URL keep working unchanged against the deployment their URL
names. Moving one to deployment-agnostic wording is a human re-seed via teach, one
signal at a time, never a bulk rewrite.

## Per-environment credentials

Secrets gain one optional level: a gitignored per-environment overlay file,
`.praxis.secrets.<env>`, a sibling of the shared `.praxis.secrets`:

```
.praxis.secrets         # shared keys live here, once
.praxis.secrets.dev2    # dev2-only values; win over the base file for their keys
.praxis.secrets.prod    # prod-only values
```

A `KEY` environment variable still beats both files, so CI keeps supplying credentials
purely as runner secrets. Keys the deployments share live once in the base file; only
the values that differ go in an overlay. `praxis init` adds the `.praxis.secrets.*`
gitignore line.

## Per-environment sessions

Saved authenticated sessions ([the two-factor walkthrough](auth-and-2fa.md)) are
domain-bound: cookies for prod's host do nothing on dev2's. So with an environment
selected, the session for a role resolves env-scoped and ONLY env-scoped:

1. the `PRAXIS_AUTH_STATE_<ENV>_<ROLE>` environment variable (environment and role
   uppercased: `PRAXIS_AUTH_STATE_DEV2_USER`)
2. the file `.praxis.auth/<env>/<role>.json` (for example `.praxis.auth/dev2/user.json`)

There is deliberately NO fallback to the unscoped `PRAXIS_AUTH_STATE_<ROLE>` /
`.praxis.auth/<role>.json` sources: a prod session silently injected into a dev2 run
would yield a confusing AUTH-EXPIRED at best and a run against the wrong logged-in
surface at worst. A missing env-scoped session is a loud MissingSession naming the role
AND the environment, routed through the unchanged AUTH-EXPIRED contract.

Seed one session per role per environment with a real login through teach against each
deployment: a teach login against dev2 saves `.praxis.auth/dev2/user.json`, one against
prod saves `.praxis.auth/prod/user.json`. One human login per (role, environment),
reused by every later run on that deployment.

This also means: when an existing project DECLARES environments, its old unscoped saved
sessions stop resolving until they are moved into `.praxis.auth/<env>/`. The
MissingSession message names the new path.

## Run it

```
praxis regress --env dev2
praxis regress --env prod
```

Each run checks one environment, and everything a run produces is tagged by it:

- the run directory is `runs/<timestamp>__dev2/` (still timestamp-sorted, and `ls`
  finds "the last prod run")
- the aggregate report header names the environment
- the JUnit suite name is `praxis-regress[dev2]`, so a CI matrix's legs stay
  distinguishable in the test UI

OK / REGRESSED / STALE / AUTH-EXPIRED / ERROR semantics and the loud non-zero exit are
unchanged per run. Each environment gates independently; see
[Running in CI](ci.md) for the job matrix over `PRAXIS_ENV`.

## What the partition means

Evidence is per-environment: events record the environment they were observed on, and
the believed state for a goal is projected per environment. Two consequences worth
holding onto:

- **A prod green never masks a dev2 red (or vice versa).** The two deployments'
  observations never meet in one projection, so a regression on one deployment cannot
  hide behind a healthy run on the other, and a dev2 deploy can no longer prematurely
  stale healthy prod knowledge through a shared decay anchor.
- **An environment adds ZERO oracle corroboration.** The same agent observing the same
  signal on dev2 and on prod is one perspective looking at two configurations of one
  codebase, not two independent sources: whatever systematic error fools it on dev2
  fools it identically on prod. Running `regress --env dev2` then `regress --env prod`
  mints no diversity and promotes nothing. Seeds (human / spec knowledge) fold into
  EVERY environment's projection, because a seed is product intent; belief earned
  purely from agent evidence on one environment does not transfer to the other, so
  "only ever confirmed on dev2" reads as exactly that.

One report shape is new and honest: the same goal MAY read OK on prod and STALE on
dev2. That is the deployment-skew signal itself (dev2 shipped a change prod has not),
it is exactly what a QA team wants to see ahead of a prod deploy, and it is
non-blocking by construction (STALE alone never fails a run; see the
[STALE warning on the CI page](ci.md)). Knowledge is shared, so the re-seed timing is a
human call: re-seed now if the leading environment is the new contract, or when the
change ships if the trailing one still is.

Explore findings work the same way: candidates stay one shared committed tree, and
review annotates each finding with where it was seen ("seen on dev2 only"), which is
exactly the datum you need to decide whether a finding is product-level or just not
shipped to prod yet. Promotion stays a human merge; the annotation adds information,
never corroboration.

## Migrating an existing single-tree project

Nothing to do to stay single-env. To adopt environments:

1. In `.praxis/config.yaml`, add the `environments` map (move the current `base_url`
   under the environment that URL actually points at, add the others) and a
   `default_env`. Optionally move `observed_app_version` per environment.
2. Move `.praxis.auth/<role>.json` to `.praxis.auth/<env>/<role>.json` for the
   environment those cookies belong to.
3. Split any env-specific lines of `.praxis.secrets` into `.praxis.secrets.<env>`; keep
   shared keys in the base file. Re-running `praxis init` adds the `.praxis.secrets.*`
   gitignore line idempotently.
4. Optional: set `legacy_env` (last section) if historical run-log evidence should be
   attributed to one environment.
5. Goals that embed a URL keep working against that deployment; re-seed their wording
   via teach at your own pace.

## Migrating two sibling trees

If you are already running the duplicated layout this feature exists to kill (say
`envs/dev2/.praxis/` and `envs/prod/.praxis/`), the merge into one repo-root `.praxis/`
is documented, manual, and has exactly one risky step:

1. **Reconcile the knowledge (the human step, and the risky one).** Diff the two
   `knowledge/` trees. Hand-duplicated goal YAMLs will have drifted; for each goal,
   pick or merge ONE canonical product version, strip deployment-coupled wording
   (embedded hosts), and place one file under `.praxis/knowledge/`. Where the two trees
   genuinely differ because dev2 is ahead, keep the version that matches the deployment
   whose behavior is still the contract, and expect STALE on the other until the skew
   closes. This is a human seed judgment; no command can make it safely, which is why
   Praxis ships no migration command.
2. **Config:** one `config.yaml` with both environments' `base_url`s plus
   `default_env`.
3. **Candidates:** copy the candidate files from BOTH trees into
   `.praxis/candidates/<goal>/`; one file per event id, no collisions by construction.
   Pre-migration candidates show env-unannotated in review; the immutable files are not
   rewritten.
4. **Sessions and secrets:** each tree's `.praxis.auth/<role>.json` moves to
   `.praxis.auth/<env>/<role>.json`; secrets merge per the single-tree steps above.
5. **Runs:** drop them (gitignored, regenerable). In CI, replace the two cwd-based jobs
   with one job matrix over `PRAXIS_ENV` ([Running in CI](ci.md)).

## Attributing old evidence: legacy_env

Events written before the map was declared carry no environment, and by default they
match NO declared environment: you do not know which deployment produced them, so they
are honestly excluded from every environment's projection. If you DO know (the whole
history came from one deployment), say so in config:

```yaml
legacy_env: prod
```

That attributes the old events to `prod` as a projection input: deterministic,
reversible by deleting the key, and no event file is ever rewritten. Since run logs are
regenerable and regress evidence is non-promotable anyway, most migrations can simply
not care.
