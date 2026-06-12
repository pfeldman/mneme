# ADR-0035: Multi-environment deployments: product-level knowledge, per-environment evidence

Status: Proposed

## Context

The first external consumer of the library is a QA repo for a SaaS admin app
deployed twice: a dev/staging instance (dev2) and prod. Same product, same
goals, different base URL and different credentials per deployment. Today
that repo needs TWO sibling `.praxis/` trees selected by cwd, with every goal
YAML duplicated by hand, because three mechanisms are single-deployment:

- `discover_project` walks up from cwd to the first `.praxis/config.yaml`
  and is the only project-resolution mechanism; `praxis regress` /
  `praxis explore` have no `--env` flag and no env-var config override.
- `config.yaml base_url` is inert at runtime: only `praxis status` prints
  it. It never reaches the adapter, the agent prompt
  (`runner/prompts.py`), or the console brain preamble
  (`cli/claude_brain.py`), so the deployment URL has to live in the text of
  every goal, hard-coupling product knowledge to one deployment.
- Secrets and saved sessions resolve by the same walk-up
  (`.praxis.secrets`, `.praxis.auth/<role>.json`, ADR-0021 decision 6,
  ADR-0026 decision 3), so credentials are per-tree too.

Duplicated goal YAMLs are exactly the drift this project exists to avoid:
two copies of one product's operational knowledge, edited independently,
disagreeing silently. The fix must let ONE set of goal YAMLs run against N
environments with zero duplication, while answering the question the prior
ADRs never had to face: what happens to BELIEVED state, the oracle gate
(ADR-0005, ADR-0008, ADR-0029), the decay anchors (ADR-0013), and the
candidate store (ADR-0014, ADR-0021) when the same goal is observed on two
deployments that legitimately differ (dev2 runs ahead of prod).

Two prior patterns are load-bearing and are mirrored here: the ADR-0021
decision 6 env-wins-over-file secrets channel, and the ADR-0034
flag > env var > committed config precedence for an operational input. One
existing field matters: `Target.environment` already exists in the schema
and the model as a plain label; this ADR generalizes it rather than
inventing a parallel concept. The full design analysis, including the
options that were rejected, lives in `tasks/multi-env-support/analysis.md`.

## Decision

### 1. Environments are a committed config map; knowledge files stay product-level and environment-free.

`.praxis/config.yaml` gains an optional `environments` map and a
`default_env`:

```
environments:
  dev2:
    base_url: https://dev2.example.com
    observed_app_version: 2.6.0     # optional per-env decay anchor
  prod:
    base_url: https://example.com
    observed_app_version: 2.3.0
default_env: dev2
```

Everything under `.praxis/knowledge/` remains product-level: goals,
success / failure signals, risks, uncertainties, `auth_state`. A goal is a
capability of the PRODUCT; which deployment a run checks it on is an
operational input, exactly like the brain model (ADR-0034 decision 1). In a
project with a declared map, knowledge files leave `target.environment`
unset; the selected environment is stamped onto run records at run time,
never onto knowledge. The environment NAME is operational provenance, like
`agent_identity`: not a secret, not PII, and it never enters an assertion.

A project whose config.yaml has NO `environments` key is an "undeclared"
project and behaves exactly as today: no flag, no new files, same paths,
same env-var names, same rendered prompts, same exit codes. This
zero-ceremony bar is a hard requirement of this ADR, pinned by tests.

### 2. The environment is selected per run with the ADR-0034 precedence; resolution failures are loud.

Selection, fixed: `--env` flag (on `regress`, `explore`, `status`) >
`PRAXIS_ENV` env var > `config.yaml default_env` > a single-entry map
auto-selects its only entry. An empty string at any level counts as unset
(ADR-0034 posture). A declared multi-entry map that resolves to nothing is a
LOUD error naming the declared environments and the three ways to pick one;
an unknown name from the flag or the env var is a LOUD error too. `--env`
on an undeclared project errors loudly; `PRAXIS_ENV` on an undeclared
project is ignored with a one-line stderr notice, so a pipeline-wide export
cannot break repos that have not adopted environments. The resolved
environment and its winning source are printed once per run on stderr, the
same banner posture as the ADR-0034 model pin.

### 3. The selected environment's base_url reaches the agent at run time.

The CLI threads the selected environment's `base_url` through the engine to
the prompt renderers, which add an explicit line ("App under test:
<base_url>" plus the instruction that "the app under test" in a goal means
THIS deployment), and the console brain preamble carries the same line.
Goals can therefore say "the app under test" instead of hardcoding a URL.
The URL is a run input; it is never written into a knowledge file, a
candidate, or an assertion.

The injection happens ONLY when an environment is selected from a declared
map: every existing single-env project carries a scaffolded, possibly-dead
`base_url` default, and injecting an unvalidated URL into prompts that
worked yesterday is the kind of silent behavior change this project
forbids. A single-env project opts in by declaring a one-entry map.

Existing goals that embed URLs keep working unchanged against the
deployment their URL names; going deployment-agnostic is a per-signal human
re-seed via teach (ADR-0022), never a bulk rewrite (the ADR-0033 posture).
The teach skill guidance inverts for new seeds: goal text and signal values
are deployment-agnostic, `url`-type signals are path-shaped, and the
environment name never appears inside a signal value.

### 4. Evidence is per-environment: an environment field on events, and the projection partitions by it.

`ObservationEvent`, `RegressObservationEvent`, `CandidateEvent`, and
`DecayEvent` gain an optional `environment: str | None` field (None on
every pre-existing event file, which stays valid). The believed projection
for a goal is computed PER ENVIRONMENT: the adapter filters the event log
to the selected environment's events before projecting, and the seeds fold
into EVERY environment's projection (a human/spec seed is product intent,
trusted from cold start in each deployment, ADR-0005). The store SPI,
`merge/`, and `oracle/` are untouched: the core learns about environments
only as a field on data it never interprets, and within one environment
ADR-0005, ADR-0008, ADR-0012, ADR-0013, and ADR-0029 apply verbatim.

When a project newly declares environments, historical events
(`environment: None`) match no declared environment by default; an optional
`legacy_env: <name>` config key attributes them to one declared environment
as a projection INPUT (deterministic, no event is rewritten; the same
posture as the ADR-0013 caller-supplied anchor).

### 5. An environment is NOT a source dimension; cross-environment corroboration is structurally impossible.

This is the heart of the ADR. The same agent observing the same signal on
dev2 and on prod is ONE perspective, not two: both observations come from
the same model with the same prompt lineage looking at two configurations
of one codebase, and they share every systematic error (the ADR-0034
false-alarm class). Counting an environment as a source would let one agent
run `regress --env dev2` then `regress --env prod` and mint two
"independent" sources - a self-certification loophole with exactly the
shape of ADR-0008's `single_source_two_types` breach, with environment
standing in for type, reopening sideways what ADR-0029 closed.

An environment IS an independent deployment surface, and that is why it
earns a PARTITION rather than a vote: it changes which world an observation
describes, not how many perspectives observed it. Two deployments can
legitimately disagree (dev2 ships ahead of prod), so folding their evidence
double-counts when they agree and manufactures false `contested` when they
honestly differ. Under decision 4 the question never reaches the gate:
per-environment projections mean dev2 and prod observations never meet in
one surviving set, `source_id` stays `agent_identity` (ADR-0012) untouched,
environment-corroboration counts for nothing, and belief earned purely from
agent evidence on one environment does not transfer to another (a seed
does, because a seed is product intent). The ADR-0008 INHERENT boundary
(seed plus one genuine different-type agent observation) exists per
environment, exactly as before.

This also fixes the decay collision concretely: with dev2 on 2.6.0 and prod
on 2.3.0 (N = 2, ADR-0013), a folded projection would anchor on 2.6.0 and
stale prod's fresh-yesterday 2.3.0 evidence - a healthy prod decayed by a
dev2 deploy. Partitioned, each environment's anchor derives from its own
supporting set or its own `environments.<name>.observed_app_version`
config, and `decay.py` is unchanged.

### 6. Candidates stay one shared tree; the environment rides as provenance, and review annotates it.

The committed candidate layout (`candidates/<goal>/<candidate_id>.yaml`,
one file per observation event id, ADR-0021 decision 4) is unchanged, so
concurrent adds still never merge-conflict and the dedup-by-trigger
grouping (ADR-0023 decision 8) still sees the same finding observed on two
environments as ONE finding. The `CandidateEvent.environment` field is
surfaced by `praxis review` and the explore report as an annotation ("seen
on dev2 only") - exactly the datum a human needs to decide whether a
finding is product-level or not-yet-shipped. Promotion stays a human seed
event into product-level knowledge; environment adds no corroboration
diversity (decision 5).

### 7. Per-environment secrets and sessions extend the existing channels; sessions never fall back across environments.

Secrets (extends ADR-0021 decision 6): the env var `KEY` still wins;
below it, a new gitignored per-env overlay file `.praxis.secrets.<env>`
wins over the shared `.praxis.secrets` for the keys it defines. Shared keys
live once in the base file. `praxis init` adds a `.praxis.secrets.*`
gitignore line.

Sessions (extends ADR-0026 decision 3): with an environment selected, the
resolution is `PRAXIS_AUTH_STATE_<ENV>_<ROLE>` env var, then
`.praxis.auth/<env>/<role>.json` - and NOTHING else. There is deliberately
NO fallback to the unscoped `PRAXIS_AUTH_STATE_<ROLE>` /
`.praxis.auth/<role>.json` sources: a storage state is domain-bound, and a
prod session silently injected into a dev2 run yields a confusing
AUTH-EXPIRED at best and a run against the wrong logged-in surface at
worst. A missing env-scoped session is a loud `MissingSession` naming the
role AND the environment, routed through the unchanged AUTH-EXPIRED
contract. With no environment selected, both channels resolve exactly as
today.

### 8. Runs and reports are tagged by environment; the verdict and exit-code contracts are unchanged.

A declared project's run directory is `runs/<timestamp>__<env>/` (the
timestamp prefix keeps run dirs sortable and the store treats dir names
opaquely); the aggregate markdown report header names the environment; the
JUnit suite name becomes `praxis-regress[<env>]`. One run checks one
environment; OK / REGRESSED / STALE / AUTH-EXPIRED / ERROR semantics, the
loud non-zero exit, and the per-goal budget (ADR-0023, ADR-0026, ADR-0027)
are unchanged per run. The same goal MAY honestly read OK on prod and STALE
on dev2: that is the deployment-skew signal itself, non-blocking by
construction (STALE alone never fails a run), and the regress skill triage
names the skew and the re-seed timing trade (re-seed now if the leading
environment is the new contract, or when the change ships if the trailing
one still is).

### 9. praxis init can declare the map; the skills become environment-aware.

`praxis init --environment NAME=URL` (repeatable) plus `--default-env`
scaffold the declared map (mutually exclusive with the legacy single-env
`--env` / `--base-url` pair, which keeps working). An undeclared scaffold
gains a COMMENTED `environments:` block for discoverability, the ADR-0034
`brain_model` pattern. The three skills resolve the environment first
(default `default_env`), use the env-scoped session seam, name the
environment in every report and triage line, and carry the decision 3 teach
guidance. Migration is documented, not automated: the single-tree adoption
is a config edit plus two file moves, and the two-sibling-trees merge
(`envs/dev2/` + `envs/prod/`) is a documented manual procedure whose only
risky step - reconciling hand-duplicated, drifted goal YAMLs into one
product file per goal - is a human seed judgment a command cannot make
safely (ADR-0005 spirit; ADR-0033 forbade bulk re-seeds). A helper command
is deliberately not shipped; revisit on demand.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Count an environment toward promotion diversity, or derive `source_id`
  from the environment (`agent@dev2`). One agent on N environments is ONE
  source; anything else is the ADR-0008 self-corroboration breach with a
  new coordinate (decision 5).
- Fold events from different environments into one projection, one
  surviving set, or one decay anchor. A green observation on one deployment
  must never corroborate, mask, or decay belief about another.
- Reuse the ADR-0012 tenant path convention for environments
  (`<root>/<env>/events/`). Tenancy exists to make cross-tenant reads
  unrepresentable; cross-ENVIRONMENT visibility (review annotations,
  grouping) is required, so environments are a field, never a tenant.
- Fork knowledge per environment (per-env knowledge dirs, per-env copies of
  a goal YAML, a per-env signal override block). That is the duplication
  this ADR exists to kill; deployment skew surfaces as per-env verdicts
  over ONE product knowledge set, never as forked knowledge.
- Write the environment name, the base_url, or any per-env value into a
  knowledge file, a candidate payload, an assertion, or `auth_state`. The
  environment is operational provenance on EVENTS only.
- Fall back to another environment's saved session or to the unscoped
  session when an env-scoped session is missing. Loud `MissingSession` over
  a domain-mismatched login (decision 7).
- Rewrite historical events to stamp an environment onto them. Attribution
  of legacy events is the `legacy_env` projection input; the event files
  are immutable (ADR-0001).
- Add a per-environment brain-model pin. One brain capability per project
  (ADR-0034); verdict quality must not differ by deployment.
- Make an undeclared project pay ANY new ceremony: no required flag, no new
  file, no changed path or env-var name, no changed prompt bytes, no
  changed exit code.

## Consequences

Positive:

- One set of goal YAMLs runs against N deployments. The consumer's two
  sibling trees collapse into one committed knowledge tree; goal drift
  between copies becomes unrepresentable.
- The inert `base_url` becomes the mechanism that decouples knowledge from
  deployment: goals say "the app under test" and the environment supplies
  the URL at run time.
- The oracle keeps its teeth. Environments add zero corroboration, the
  self-certification surface does not grow, and a dev2 regression can never
  hide behind a prod green (or vice versa) because the two never share a
  projection.
- Decay anchors stop lying across deployments: the dev2-ahead-of-prod
  version skew can no longer prematurely stale healthy prod knowledge.
- Per-env verdicts over shared knowledge surface deployment skew honestly
  (OK on prod, STALE on dev2) without blocking either gate.
- Adoption is incremental and reversible: undeclared projects are
  byte-identical in behavior; declaring a one-entry map is one config
  stanza.

Negative:

- The STALE skew window is real: while dev2 and prod disagree on purpose,
  one environment's regress reads STALE until the re-seed timing question
  is settled by a human. Honest, non-blocking, but a new thing to explain;
  the skill triage wording carries the load.
- Newly written event files carry `"environment": null` on undeclared
  projects: behavior-identical, but an OLDER praxis (`extra="forbid"` event
  models) cannot parse events written by the new version. A pre-1.0
  downgrade hazard, accepted and called out in release notes.
- Declaring environments invalidates existing unscoped saved sessions until
  they are moved into `.praxis.auth/<env>/` (no-fallback rule). A
  one-time, documented move; the MissingSession message names the new path.
- Belief earned purely from agent evidence on one environment does not
  transfer to another; a team that relies on agent-earned (non-seeded)
  belief pays the corroboration cost once per environment. Intended: "only
  ever confirmed on dev2" should read as exactly that.
- Per-key and per-role resolution gains one more level (the `.<env>`
  overlay / subdir), one more thing the skills and docs must teach.
- The prompt-injection efficacy ("App under test: <url>" actually steering
  the agent) is empirical and must be proven on the live dogfood target
  before Accepted, the same live-proof discipline as ADR-0027/0033/0034.

Invariants respected:

- `append-only-store-no-mutation` (ADR-0001): additive optional event
  fields only; legacy attribution is a projection input, never a rewrite;
  the committed history rules of ADR-0021 are untouched.
- `no-self-corroboration-source-independence` (ADR-0008, ADR-0012,
  ADR-0029): `source_id = agent_identity` unchanged; environments add no
  diversity; per-env partitions make cross-env stacking unrepresentable.
- `first-oracle-must-be-seeded` (ADR-0005): seeds fold into every
  environment's projection from cold start; nothing else transfers belief
  across environments.
- `no-secrets-tokens-pii-in-knowledge` (ADR-0017, ADR-0021, ADR-0026): the
  environment name is operational provenance, never inside an assertion;
  per-env secrets and sessions stay in the gitignored channels with the
  same never-echo rules.
- `loud-and-traceable-over-silent-and-convenient`: unresolvable or unknown
  environments fail loudly; the resolved env and its source are printed per
  run; missing env-scoped sessions are loud rather than silently borrowed;
  legacy events are excluded rather than silently re-attributed.
- `concurrent-writes-lose-no-knowledge` (ADR-0012, ADR-0021): the
  file-per-event and one-file-per-candidate layouts are byte-compatible;
  merge behavior is unchanged.
- `runtime-agnostic-core` (ADR-0003): `model`/`store`/`merge`/`oracle`
  learn about environments only as a data field; selection, filtering, and
  threading live in the CLI and adapter layers.

Invariants this ADR does NOT cover:

- The verdict taxonomy, per-goal budget, and loud exit: owned by ADR-0023 /
  ADR-0026 / ADR-0027, consumed unchanged per run.
- The matching and evidence contract: owned by ADR-0033, unchanged.
- The brain model pin: owned by ADR-0034, unchanged (and explicitly not
  per-env).
- CI mechanics beyond the `PRAXIS_ENV` variable: ADR-0024 stands; a
  multi-env pipeline is the team's own job matrix over one variable.

## Relation to prior ADRs

Amends ADR-0021 (the `.praxis/` convention, Accepted): "one repo per
project, repo boundary = tenant boundary" stands - the project is the
PRODUCT, and N deployments of one product are one tenant in one repo. The
amendment is that 0021's layout implicitly assumed one deployment per repo;
this ADR adds the committed `environments` map, the `.praxis.secrets.<env>`
overlay on decision 6, and the `.praxis.auth/<env>/` subdirs. Decisions 1-5
of ADR-0021 are unchanged. No other part is superseded.

Extends ADR-0026 (session reuse, Proposed): the session channel gains the
environment dimension (`PRAXIS_AUTH_STATE_<ENV>_<ROLE>`, per-env files)
with the same env-wins-over-file shape and a deliberate no-cross-env
fallback; AUTH-EXPIRED semantics are unchanged.

Mirrors ADR-0034 (brain model pin, Accepted): the same
flag > env var > committed config precedence and the same
banner-on-stderr / loud-on-invalid posture, applied to a second operational
input; and explicitly declines a per-env model pin.

Refines ADR-0013 (recency decay, Accepted): the decay anchor becomes
per-environment by partitioning the supporting set; `select_current_version`
and the thresholds are unchanged, and the per-env
`observed_app_version` config key is the caller-supplied anchor's
per-environment home.

Upholds ADR-0005, ADR-0008, ADR-0012, and ADR-0029: environments are
excluded from every independence and promotion rule by construction;
`source_id = agent_identity` and the non-promotability of regress
confirmations carry over unchanged.

Extends ADR-0023 (dual surface and report, Accepted): the aggregate report
and JUnit output gain the environment tag; the verdict set and the
loud-failure contract are unchanged. Extends ADR-0022 (teach, Accepted)
with the deployment-agnostic seeding guidance. Does not supersede any prior
ADR.
