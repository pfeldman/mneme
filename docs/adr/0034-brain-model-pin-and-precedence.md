# ADR-0034: The brain model is an operational input, pinned per project with a fixed flag-env-config precedence

Status: Proposed (2026-06-11)

## Context

ADR-0019 made the reasoning brain pluggable behind one seam
(`Callable[[str], dict]`), and ADR-0027 added the third execution path: the
console `praxis regress` / `praxis explore` drive a headless `claude -p`
subprocess on the user's subscription. The brain factory
(`cli/claude_brain.py: make_claude_brain`) has carried a `model` parameter
since that build, appending `--model <value>` to the `claude -p` argv, but
NOTHING set it: every console run executed on whatever model the claude CLI
happened to default to on that machine.

Dogfooding against the real target app (2026-06-11, library 0.0.4, by-ref
matching per ADR-0033) A/B'd the SAME believed goal across models and showed
that the model choice materially affects VERDICT QUALITY, not just speed or
cost. Sonnet 4.6 returned OK in about 2.5 minutes with correct evidence; the
CLI-default frontier model returned OK in about 1.5 minutes; Haiku 4.5
produced a FALSE ALARM: it opened the wrong chat widget on the page (the
third-party support chat instead of the product's own), could not interact
with it, and then HONESTLY reported the seeded signals absent. The agent did
not lie; it mis-navigated, and the run surfaced as REGRESSED on a healthy
app. The risk of a cheaper model is therefore on the navigation / grounding
side, upstream of the matching contract ADR-0033 hardened: no evidence rule
can save a verdict when the brain grounded its honest evidence in the wrong
part of the app.

This has a team-level consequence: the knowledge is shared through git
(ADR-0021), but the brain capability was not. Two teammates regressing the
SAME committed knowledge with different claude CLI defaults can reach
different verdicts; a CI runner with yet another default diverges again. The
operator also wants to pin a cheaper model per project deliberately (a regress
suite that runs on a subscription has a real quota cost, ADR-0027
consequences), which today is only possible through the ANTHROPIC_MODEL env
var the subprocess inherits: invisible to the repo, not per-environment, and
easy to forget in CI.

This ADR fixes how the model the console brain runs with is selected. It does
not touch the verdict contract (ADR-0023), the matching contract (ADR-0033),
the session model (ADR-0026), or the brain seam itself (ADR-0019).

## Decision

### 1. The model is an OPERATIONAL input to the brain, never knowledge.

Which model drives a run is execution configuration, exactly like `--headed`
or the per-goal budget: it shapes how a verdict was produced, not what is
true about the app. It therefore NEVER enters a knowledge file, a candidate,
an assertion, or any committed `.praxis/` artifact as a quality claim.
Knowledge stays brain-agnostic (ADR-0019): a signal seeded under one model is
the same signal under another. The model name is not a secret (unlike the
session or a credential) and may appear in run output and run-scoped records;
the resolved pin and its source are printed once per run on stderr so the run
output records which brain capability produced the verdicts.

### 2. Three surfaces select the model, with a fixed precedence.

- **`brain_model` in `.praxis/config.yaml`** (optional, absent by default):
  the committed, per-project pin teammates share through git. `praxis init`
  scaffolds the key COMMENTED OUT with a one-line warning, so it is
  discoverable without forcing a choice.
- **`PRAXIS_BRAIN_MODEL` env var**: the CI channel. A runner exports it and
  every console run in that pipeline uses it, mirroring the env-over-file
  precedence of the ADR-0021 decision 6 secrets channel (CI configures through
  the environment, local use through the file).
- **`--model` flag on `regress` and `explore`**: the explicit per-run
  override, for A/B-ing a candidate pin before committing it.

Precedence, fixed: `--model` flag > `PRAXIS_BRAIN_MODEL` env >
`config.yaml brain_model` > unset. Unset at every level appends NO `--model`
to the `claude -p` argv: the claude CLI's own default runs, byte-identical to
the pre-ADR-0034 invocation, so a project that never pins sees no change.
An empty string at any level counts as unset, so an exported-but-blank env
var cannot mask the committed pin.

### 3. The pin is committed per project because verdict quality must be reproducible across operators.

The false-alarm finding is why the pin belongs in the committed config and not
only in per-operator environments: teammates and CI must regress the same
knowledge with the SAME brain capability, or verdicts diverge on identical
knowledge and a false alarm on one machine reads as a real regression to the
team. A cheaper pin is legitimate (subscription quota is the binding cost,
ADR-0027), but it is a deliberate, reviewed, committed choice, validated with
a few runs against a known-good app state before it is trusted; never an
accident of whichever CLI default a machine carries.

### 4. The value is a verbatim pass-through; the claude CLI is the authority on model names.

The resolved value travels untouched to `claude -p --model <value>`. Praxis
validates NOTHING about it: no allow-list, no pattern, no alias table. Model
names rot faster than this library releases; a baked-in list would reject
tomorrow's valid model or accept yesterday's retired one. The claude CLI
errors loudly on an unknown model, the subprocess exits non-zero, and the
existing ADR-0027 decision 4 contract turns that into a loud per-goal ERROR;
a typo'd pin can never produce a silent green.

### 5. Resolution lives in the CLI layer; the core and the skill surface are out of scope.

The precedence resolves in `src/praxis/cli/` (where the brain is constructed,
`_resolve_brain_model` + `_select_console_brain`); `runner/`, `model/`,
`store/`, `merge/`, and `oracle/` never learn that models exist. The skill
surface is explicitly NOT covered: the skill brain IS the interactive Claude
session the user is already in (ADR-0019), so there is no subprocess and no
`--model` argv to pin; pinning a model there would mean re-launching the
user's own session, which is not Praxis's call.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Persist the model into knowledge, a candidate, or an event as a quality
  claim (for example a per-signal "verified-by-model" field, or a confidence
  bonus for a stronger model). Which brain ran is execution provenance at
  most (ADR-0019); an assertion's trust comes from evidence diversity and
  seeding (ADR-0005), never from the model that produced it.
- Hardcode a model name as a default anywhere in code or scaffolding. The
  default at every level is UNSET, and unset means the claude CLI's own
  default; the scaffolded config comment names no real model. A baked-in
  default rots and silently overrides the CLI the day they disagree.
- Validate the model value against a model-name list. Verbatim pass-through;
  the claude CLI is the authority and fails loudly (decision 4).
- Add per-goal model overrides (a `model` key on a knowledge file, a per-goal
  map in config). One brain per run: a goal's verdict must not silently
  depend on which goal it is, and a model key inside a knowledge file would
  put an operational input inside knowledge, violating decision 1 twice over.
- Treat the model name as a secret. It is operational configuration; hiding
  it would obscure exactly the reproducibility this ADR exists to provide.

## Consequences

Positive:

- A project can pin a cheaper model deliberately and commit the choice, so
  every teammate and CI regress with the same brain capability and the same
  subscription-cost posture. The Haiku-class false alarm becomes a reviewed
  tradeoff instead of a per-machine accident.
- A/B runs are first-class: `praxis regress --model X` compares a candidate
  pin against the committed one without touching the repo or the environment.
- CI pins through one env var with the same precedence shape the secrets
  channel already taught operators (env wins over file).
- Unset everywhere is byte-identical to today's argv, so adoption is
  zero-cost and nothing breaks for projects that never pin.

Negative:

- A committed pin can rot: when the named model is retired, every console run
  fails loudly until a human updates one line of config. Loud and traceable
  over silent and convenient; the alternative (a fallback model in code) is
  the hardcoded default decision 4 forbids.
- The pin governs only the console `claude -p` path. A skill-surface regress
  still runs on whatever model the interactive session uses, so a team that
  triages through the skill can still see model-induced divergence; the
  boundary is stated (decision 5), not solved.
- Verdict reproducibility is improved, not guaranteed: the same model is
  still a sampled agent, and two runs of the same pin can navigate
  differently. The pin removes the largest controllable divergence, not all
  of it.

Invariants respected:

- `brain-agnostic-core` (ADR-0019): resolution lives entirely in `cli/`;
  `runner/` and `model/` never import or mention models, and the brain seam
  signature is unchanged.
- `no-silent-success-when-app-broken` (ADR-0023): an unknown model is a
  non-zero `claude -p` exit, which is already a loud per-goal ERROR; no
  validation layer exists to swallow it.
- `no-secrets-tokens-pii-in-knowledge` (ADR-0017, ADR-0021): the model name
  is not a secret and still never enters knowledge; the secrets channel is
  mirrored only in precedence shape, not in handling.
- `loud-and-traceable-over-silent-and-convenient`: the resolved pin and its
  winning source are printed per run; a rotted pin fails loudly rather than
  falling back silently.

Invariants this ADR does NOT cover:

- The verdict contract, the per-goal budget, and the loud exit: owned by
  ADR-0023 and ADR-0027. This ADR only selects which model the existing
  invocation names.
- The matching and evidence contract that decides what the agent's report
  means: owned by ADR-0033. A pinned model changes who navigates, not how
  confirmations are evaluated.
- The CI brain wiring and runner secrets: owned by ADR-0024 and ADR-0026.
  `PRAXIS_BRAIN_MODEL` is one more env var a runner exports, not a new CI
  mechanism.

## Relation to prior ADRs

Extends ADR-0027 (console test runner and claude -p brain, Accepted): the
`model` parameter that ADR-0027's build left dormant on `make_claude_brain`
gains its three selection surfaces and fixed precedence; the invocation,
budget, and loud-ERROR contracts are consumed unchanged.

Extends ADR-0019 (brain pluggability, Accepted): the model is execution
configuration of one brain implementation, resolved where that brain is
constructed (the CLI layer); the core stays brain-agnostic and the skill
surface keeps its own session as its own brain.

Mirrors ADR-0021 decision 6 (secrets channel, Accepted) in precedence shape
only: environment wins over the committed file for CI ergonomics, but the
model name is operational config, not a secret, and lives IN the committed
config rather than outside it.

Complements ADR-0033 (confirmation by identity with mandatory evidence,
Proposed): ADR-0033 hardened what an honest report proves; this ADR pins who
produces the report, because the dogfooded false alarm was honest evidence
grounded in the wrong widget, a failure upstream of matching.

Does not supersede any prior ADR.
