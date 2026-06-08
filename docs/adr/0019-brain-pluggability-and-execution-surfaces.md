# ADR-0019: Brain pluggability and execution surfaces

Status: Accepted (2026-06-08)

## Context

ADR-0018 reframed Phase 3 as a library plus git, no SaaS, and named seven
owned items. This ADR owns the first and most foundational of them: how a
brain plugs into the Praxis body, and which execution surface each operation
is delivered on. The rest of the Phase 3 batch (ADR-0020 through ADR-0025)
depends on the split fixed here.

The hard constraint that drives the whole reframe is local cost. A QA
engineer running Praxis on their own machine must not be forced to hold an
Anthropic API key and pay per token to teach, regress, or explore. The
local brain is Claude Code, delivered as skills, reasoning on the user's
existing subscription with no API key in the loop. The paid path (an
API-key agent) is the live extra, reserved for CI where there is no human
to drive a subscription session. This is the "library plus git, local
brain free, CI brain paid" posture ADR-0018 section 1 records.

There is already precedent for keeping the reasoning runtime out of the
core. ADR-0003 put every browser runtime behind a two-method adapter SPI
(`read_knowledge`, `write_observations`) and made the core
(`model`, `store`, `merge`, `oracle`) zero-runtime-dependency. The
existing `pyproject.toml` declares two optional extras, `browser-use` and
`live` (anthropic), so the body already separates "what is always
installed" from "what a particular run needs". This ADR extends that same
discipline from the browser runtime to the brain: the core stays not only
runtime-agnostic but brain-agnostic, and the brain ships as an optional,
swappable surface rather than a baked-in dependency.

The Praxis operations are not uniform. Some read the store and report; they
do not reason and need no brain at all. Others decide what to click, what
to ask, and what counts as success; they reason and need a brain. Phase 3
must classify the operations on that axis before it can decide which
surface each one is delivered on, because a deterministic operation can be
a plain CLI command while an agentic one needs a brain behind it.

## Decision

### 1. The body is the library; the brain is pluggable.

Praxis the library is the **body**: browser control (the ADR-0003
adapters), the knowledge store and projection (ADR-0001), and the
deterministic CLI. The body has zero brain dependency and is fully testable
without any LLM, exactly as ADR-0003 made it testable without a browser.

The **brain** is the LLM that reasons: it decides what to click, when it is
stuck, what to ask the human, and whether the happy path was observed. The
brain is pluggable. It is selected per execution surface, never compiled
into the core, and never recorded in knowledge. The body exposes the same
store and the same adapters to whichever brain drives it.

### 2. Operations split into deterministic and agentic classes.

The CLI operations partition on whether they reason:

- **Deterministic operations: `init`, `status`, `review`.** These read and
  report. The init operation scaffolds the `.praxis/` tree and the skills
  (ADR-0021, ADR-0020). The status operation reads the projection and
  prints believed / contested / stale / quarantined counts. The review
  operation surfaces the contested-candidate queue with provenance
  (ADR-0014). None of them decides what to click or what is true; they are
  plain CLI commands and need no brain.
- **Agentic operations: `teach`, `regress`, `explore`.** These reason
  against a live app. The teach operation explores until it can perform a
  happy path and asks the human when blocked. The regress and explore
  operations drive the app to re-check believed signals and hunt
  off-happy-path. Each needs a brain to operate.

The classification is the gate for surface selection in decisions 4 and 5:
deterministic operations stay bare CLI, agentic operations get a brain.

### 3. The two brains and their cost model.

A brain plugs in through exactly one of two surfaces:

- **Local brain: Claude Code via skills.** No API key. The reasoning runs
  inside a Claude Code session on the user's own subscription, so the
  marginal cost of a local teach / regress / explore is zero beyond that
  subscription. This is the default development path. The skills are
  scaffolded into the project's `.claude/skills/` by `praxis init`
  (mechanism owned by ADR-0020).
- **CI brain: an API-key agent (the `live` extra).** A headless agent
  driven by an Anthropic API key, installed via the existing `live`
  optional extra. This is the only paid surface and exists because CI has
  no human subscription session to borrow. It is the brain the console
  `praxis regress` and `praxis explore` use when run autonomously, for
  example wired into a team's CI (ADR-0024).

The cost asymmetry is deliberate: local use is free on a subscription, the
API key is the live extra paid for CI throughput. Choosing a brain never
changes what is stored; it only changes who pays and where the reasoning
runs.

### 4. Agentic operations have a dual surface.

The regress and explore operations are delivered on TWO surfaces over the
SAME engine:

- A **console CLI** (`praxis regress`, `praxis explore`): test-style
  red / green output, a process exit code, no chat. This is what CI runs
  and what a user scripts. With the CI brain it is fully automatable.
- A **Claude Code skill** (`/praxis:regress`, `/praxis:explore`): the same
  underlying engine, plus the local Claude brain triaging failures
  interactively. This is the free local path.

Same body, same store reads and writes, same operation semantics; the only
difference between the surfaces is which brain drives them and whether the
output is a process exit code or an interactive triage. The break-vs-drift
report contract that distinguishes both surfaces is owned by ADR-0023.

### 5. The teach operation is the exception: skill-only.

The teach operation is delivered ONLY as a Claude Code skill
(`/praxis:teach`), never as a bare CLI command. It is always
human-in-the-loop: the agent asks the user interactively when blocked and
the user confirms the result before it is written. There is no autonomous
console `praxis teach` and there is no CI teach, because there is no human
in CI to answer the interactive prompts. The teach output is human-seeded
knowledge, the legitimate ADR-0005 first-oracle seed path; an autonomous
CLI teach would produce self-certified oracles and break that rule. The
full teach protocol is owned by ADR-0022; this ADR fixes only that teach is
skill-only and why.

### Forbidden alternatives

DO NOT, in any Phase 3 ADR or implementation:

- Force an API key for local use. The local brain is Claude Code on the
  user's subscription with no API key; the paid API-key agent is reserved
  for CI (the `live` extra).
- Bake a single brain into the core. The brain is selected per surface and
  is swappable; the core stays brain-agnostic, the same way ADR-0003 kept
  it runtime-agnostic.
- Make the core depend on Claude Code. The deterministic core and the body
  must import and test with no Claude Code and no LLM present; Claude Code
  is one brain surface, not a core dependency.
- Persist the brain choice into knowledge. Which brain produced an
  observation is execution provenance at most (`source_id = agent_identity`
  per ADR-0009 / ADR-0014), never a stored field that the projection or the
  oracle gate reads. Knowledge is brain-independent.
- Deliver the teach operation as an autonomous CLI command or run it in CI.
  Teach is skill-only and human-in-the-loop, so its output stays a
  legitimate human seed (ADR-0005).

## Consequences

Positive:

- Local Praxis costs nothing beyond the user's existing Claude Code
  subscription. The barrier of "hold an API key and pay per token" is
  removed for the development path, which is the adoption posture ADR-0018
  bet on.
- The core stays brain-agnostic the same way ADR-0003 made it
  runtime-agnostic. A future brain (a different agent, a different
  subscription tool) is additive: it plugs in as a new surface without
  touching the store, the projection, or the oracle.
- Deterministic operations stay simple. The init, status, and review
  commands have no LLM in the loop, so they are fast, scriptable, and
  testable with zero brain present.
- The dual surface lets the same engine serve free local triage and paid CI
  automation without forking the operation logic; only the driving brain
  and the output shape differ.

Negative:

- Two brain surfaces is two integration paths to keep working. A change to
  the agentic operations must be exercised both as a Claude Code skill and
  as a console CLI run; ADR-0023 carries that dual-surface test burden.
- The free local path depends on Claude Code specifically. If a user has no
  Claude Code subscription, they fall back to the paid API-key brain for
  every agentic operation; the "free" property is conditional on the
  subscription, not universal.
- Skill distribution via a pip wheel is novel (the skills ride the package
  and `praxis init` scaffolds them). The mechanism is owned by ADR-0020 and
  carries its own implementation risk, recorded there.

Invariants respected:

- `runtime-agnostic-core` extended to `brain-agnostic`: ADR-0003 kept the
  core free of any browser runtime; this ADR keeps it free of any single
  brain. The brain plugs in per surface, the core imports and tests with no
  LLM, and knowledge never records which brain ran.
- `adapter-spi-tiny-and-stable`: the brain plugs in above the body, not into
  the adapter SPI. The two-method SPI (`read_knowledge`,
  `write_observations`) from ADR-0003 is unchanged; a brain reads and writes
  knowledge only through that boundary, so it stays minimal and stable.
- `loud-and-traceable-over-silent-and-convenient`: the deterministic-vs-
  agentic split and the skill-only teach rule are named explicitly so no
  later ADR can quietly slip an autonomous CLI teach or a self-certified
  oracle past the human-seed requirement.

Invariants this ADR does NOT cover:

- `schema-is-single-source-of-truth` under packaging, and the
  skill-distribution and wheel mechanics: owned by ADR-0020. This ADR fixes
  that skills are the local brain surface; ADR-0020 fixes how they ship.
- `first-oracle-must-be-seeded`, `provenance-and-confidence-mandatory`, and
  `no-secrets-tokens-pii-in-knowledge` for the teach operation: owned by
  ADR-0022. This ADR fixes only that teach is skill-only; ADR-0022 owns the
  protocol that keeps the seed legitimate.
- `no-silent-success-when-app-broken` and the OK / REGRESSED / STALE report
  contract for the dual surface: owned by ADR-0023. This ADR fixes the two
  surfaces; ADR-0023 fixes what they report.
- `no-self-corroboration-source-independence` for the CI brain candidate
  path: owned by ADR-0024. This ADR fixes that the CI brain is the API-key
  agent; ADR-0024 owns how its writes stay source-independent.

## Relation to prior ADRs

Extends ADR-0003 (runtime-specific code behind an adapter SPI, Accepted)
from runtime-agnostic to brain-agnostic. ADR-0003 kept every browser runtime
behind a small optional-extra SPI and gave the core zero runtime
dependencies; this ADR applies the same discipline to the reasoning brain,
treating it as a per-surface plug-in rather than a core dependency and
leaving the two-method SPI untouched.

Depends on ADR-0018 (Phase 3 scope and the library-plus-git reframe,
Proposed) for the scope this ADR sits in: the body-vs-brain split and the
local-free / CI-paid cost model are the concrete realization of ADR-0018
section 1's second half. The remaining Phase 3 ADRs build on the split
fixed here: ADR-0020 ships the skills and the `live` extra, ADR-0022 owns
the skill-only teach protocol, ADR-0023 owns the dual-surface report, and
ADR-0024 covers wiring the console commands into a team's CI.

Re-cites ADR-0001, ADR-0005, ADR-0009, and ADR-0014 where the brain
boundary touches them (append-only store, the first-oracle-seeded rule,
`source_id = agent_identity`, and candidate provenance); it does not
supersede any of them.
