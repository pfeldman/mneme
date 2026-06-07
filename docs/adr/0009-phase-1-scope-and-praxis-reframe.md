# ADR-0009: Phase 1 scope, regression-recall as the moat falsifier, and the praxis reframe

Status: Accepted (2026-06-07)

## Context

Phase 0 cleared its three gates with margin: at equal reliability the memory
arm finished the three happy paths in 4.667 +/- 0.471 actions vs cold's
8.333 +/- 0.943 (cost ratio 0.56, ~3.9 sigma separation, n=15/arm); robustness
vs a recorded script was 18/18 vs 3/18; oracle false_pass and false_fail both
0.0 across 68 runs and the adversarial stress harness (ADR-0008). The result
is real. What it actually measured is narrower than the project's framing has
been claiming.

What Phase 0 measured: given a SEEDED success oracle (success signals already
at `believed`), a runtime-agnostic adapter regenerates steps cheaply on the
happy path, and the projection survives cosmetic / implementation / some
semantic UI mutations without drifting to believed-garbage. The agent was not
remembering steps. It was reading what "success" means and re-deriving the
how. The cold arm in Phase 0 received only a goal string, so the 44% bundles
two distinct claims: (a) "an agent that knows what success means is cheaper",
and (b) "accumulated knowledge about THIS app's non-obvious failure modes
catches bugs a stranger would miss". Phase 0 only exercises (a). The (b)
claim is the durable one. (a) is a wasting asset: a Claude-8-class cold
agent primed with the app README will close most of that 44% on its own.

The durable value, if there is one, is operational knowledge about a specific
system: what success means here, what is risky here, what is unknown here.
A stranger to the app cannot reconstruct that from public surface. That is
the falsifiable form of "Mneme has a moat".

Activating the full reference schema (`knowledge.phase1.schema.json`) is the
schema-rot risk (docs/06): naming a field is not enough; if the experiment
does not consume the field, it is dead weight that creates new poisoning
surface. The Phase 1 schema activation must be justified field by field by
experiment consumption.

## Decision

### 1. Reframe: operational knowledge, not memory. Rename `mneme` to `praxis`.

The project is operational knowledge about a system under test, not a
memory cache. This is not branding; it changes three concrete things:

- What we measure: the Phase 1 falsifier is regression-recall at fixed token
  budget, not cost on happy paths. The happy-path cost number from Phase 0 is
  downgraded to a secondary observation, kept only as a regression guardrail.
- Which schema fields activate: risks and uncertainties become primary, not
  optional add-ons. States and paths stay deferred (see 2).
- What the product surface looks like: `praxis regress` and `praxis explore`
  are peer modes, not "regression now, exploration someday".

Package and repo rename to `praxis`. The Greek-myth lineage of `mneme`
(muse of memory) misleads readers into a recall framing. `praxis`
(Aristotelian "practical wisdom applied to a particular situation") is the
exact semantic fit and is short enough for a CLI verb root. Class names
already correct (`KnowledgeFile`, `KnowledgeAdapter`) stay. Import path moves
from `mneme.*` to `praxis.*`. Schema `$id` moves to `praxis.dev/schema/knowledge/v0`.

### 2. Phase 1 schema activation, field by field, justified by experiment consumption.

Each activated field MUST be read by R-mode or E-mode prompts, or by the
projection in a way the experiment measures. Each deferred field is named,
with reason.

- `success_signals`: KEEP (already active). Read by R-mode as the oracle.
- `failure_signals`: PROMOTE to load-bearing. Read by R-mode as anti-goals
  (a failure signal observed = regression). Written by E-mode when a
  candidate regression is found.
- `risks` (with `trigger`): ACTIVATE. Read by E-mode prompt as "where to
  probe off the happy path". This is the central experiment primitive;
  without it E-mode degenerates to "go explore" and ties cold. SEE 4 below
  for the trigger validator (the schema-rot defense).
- `uncertainties`: ACTIVATE. Two roles. (a) Agent's exit lane from R-mode
  ("I could not resolve this"), feeding the next E-mode run. (b) Human
  review queue (`praxis review`) that turns agent observations into durable
  knowledge.
- `states`: DEFER to Phase 2. Tempted to ship for state-coverage metrics in
  exploration; ruled out because the experiment falsifies the moat without
  state graphs, and adding them now invites graph-merge scope creep.
  Revisit when E-mode demonstrably needs novelty tracking.
- `paths`: DEFER to Phase 2. Phase 0's evolution result showed the agent
  regenerates path-equivalents from oracle + goal text alone. Adding paths
  now adds authoring burden with no experimental consumer.
- `refuted` status: REJECTED for Phase 1. The synthesis draft proposed
  `refuted` driven by "auditor scenarios"; the adversarial review pointed
  out this is exactly the single-source state transition ADR-0008
  hardened against. Auditor scenarios become an OFFLINE oracle correctness
  check (like `oracle_stress.py`), not a status flip. The Phase 0 four-value
  status enum (`believed` / `contested` / `stale` / `quarantined`) stays.

### 3. Two modes, one runner package: `src/praxis/runner/`.

- R-mode (regression): inputs = believed `success_signals` and
  `failure_signals` for the goal under test, plus a budget. Outputs = per-goal
  pass / fail / uncertain verdict + observations written to the event log.
  Auditor scenarios are explicitly NOT an input (the adversarial leakage
  concern).
- E-mode (exploration): inputs = `risks` (status `believed` or `contested`),
  `uncertainties`, `failure_signals` (as anti-goals to watch for), and a
  budget. Outputs = candidate failure-signal observations, new
  uncertainties, new candidate risks, all written `contested` by default.
  Promotion to `believed` requires the existing diversity-or-seed gate
  (ADR-0005, ADR-0008). Same-source repeats grant no independence; the
  runner sets `source_id = agent_identity`, not `run_uuid`, so 100 E-mode
  runs of the same model do not self-promote.

Modes live in `src/praxis/runner/` (`regression.py`, `exploration.py`,
`report.py`), NOT in adapters. The adapter SPI stays at two methods
(`read_knowledge`, `write_observations`); only the prompt renderer learns
`mode="regression" | "exploration"`. This preserves the runtime-agnostic
core invariant (AGENTS.md non-negotiable 4): the runner depends only on
model / store / merge / oracle plus an injected `KnowledgeAdapter`.

### 4. `risks.trigger` validator: structured form, not free text.

A `trigger` is the condition that activates a risk. The reference schema
let it be free text, which is the schema-rot vector (`under high load`,
`sometimes`, `race condition` are all unfalsifiable). Phase 1 narrows it:

A `trigger` MUST cite a specific observable check at write time. Two
accepted forms:

- HTTP: `<METHOD> <PATH> with <BODY|PARAMS>` and an observable response
  predicate. Example: `POST /coupon/apply with {coupon:"SAVE10", subtotal:49}
  returns 200 and applied=true`.
- Sequence: `<n>x <ACTION_INTENT>` with an observable post-condition.
  Example: `2x submit checkout returns 200 with same order_id`.

A validator at the adapter boundary rejects free-text triggers. Borderline
cases produce an LLM-judge event (traceable, not silent). Rejected
triggers do not enter the store. This narrows what `risks` can mean to
what the experiment can falsify.

### 5. Regression-recall is the Phase 1 falsifier of the moat.

Fixed token budget per arm per release (and actions + wall time as
secondary cost proxies for the subscription path). Three arms in the first
experiment run: `cold` (goal string only), `cold_readme` (goal string +
public README + one sentence per goal, written before this ADR), `memory`
(R-mode + E-mode reading the full Phase-1 knowledge). Stagehand arm is
deferred to a separate Phase 1.5 experiment with its own pre-registration;
half-committing here makes it either skipped (if memory wins) or
prompt-overfit (if memory loses), per adversarial concern 12.

Kill criteria carry sigma bounds. Pre-registered numerical thresholds
live in `docs/phase-1-experiment.md`. If the moat does not show up, Phase 1
returns to the kill/continue gate (docs/04), not to Phase 2.

### 6. The auditor protocol is offline oracle-correctness, not an input to R-mode.

For each seeded goal, a separate session (not the seed author) writes one
or two known-failure scenarios. Those scenarios run as an OFFLINE oracle
correctness check (analog to `oracle_stress.py`): a believed oracle that
"passes" a known-broken scenario produces a `contested` event via the
existing diversity-or-seed rule. There is no `refuted` status, no
single-source state flip. R-mode in the regression-recall experiment does
NOT see the auditor scenarios; that would leak ground truth to the memory
arm (adversarial concern 1).

## Consequences

+ The Phase 1 experiment falsifies the moat on the right axis. Beating
  `cold_readme` on regression recall, especially on categories a stranger
  to the app would not probe, is the only result that survives Claude-8
  closing the happy-path cost gap.
+ Schema activation is small and load-bearing: every active field is read
  in the experiment in a named way, every deferred field is named with a
  reason. Schema rot is bounded.
+ The runner is runtime-agnostic. A Stagehand adapter author does not
  have to implement R-mode or E-mode logic; only `read_knowledge` and
  `write_observations`.
+ The `risks.trigger` validator makes a wrong risk loud at write time
  (rejected), not silent at projection time (believed garbage). Aligns
  with AGENTS.md closing line.
+ The praxis rename gives the team a name that carries the actual claim.
  CLI verb root (`praxis regress`) reads as intended.

- The cost-on-happy-path number from Phase 0 is now a guardrail, not a
  headline. If `cold_readme` closes the gap (a real possibility per
  adversarial concern 8), the Phase 0 result is reframed, not falsified:
  the moat was always supposed to be regression recall, not cost margin.
- E-mode introduces real prompt-engineering risk. The version-pinned
  E-mode prompt is reported in each run manifest; any change invalidates
  prior data. An `off_path_fraction` observability metric (fraction of
  E-mode actions on URLs that R-mode did not visit) is logged per run as
  a floor check: if below 0.4, E-mode degenerated into R-mode and the
  recall number is invalid regardless of value (adversarial concern 11).
- A live API-key run on the regression-recall experiment is in scope for
  Phase 1, because the docs/06 existential risk is framed in money, not
  actions. Subscription-path runs (Claude Code as the agent via
  Playwright MCP) carry the bulk of the data; one paid API-key run
  confirms the margin translates to tokens.
- The `refuted` status and auditor protocol as R-mode input are
  deliberately deferred; the auditor scenarios still run as an offline
  oracle stress check, so the wrong-seed poisoning vector is closed,
  just without a new status enum value.
- The Stagehand benchmark promised in docs/07 is deferred to a separate
  Phase 1.5 experiment with its own pre-registration. Half-commit
  removed.

## Open gap recorded for Phase 1.5

E-mode emits candidate observations / risks / uncertainties as a typed
result, but the store's event model only carries signal observations.
Candidate risks + uncertainties survive the run only inside the
`ExplorationResult` returned to the caller; the next `praxis review`
session does NOT surface them because the projection has nothing new
to fold in. Phase 1 ships the read path (R-mode + E-mode in the live
experiment) and the data plumbing for signal observations; the
"agent-written candidates persist as contested across sessions" half
lands in Phase 1.5, alongside the Stagehand benchmark and the auditor
protocol. The signal-side of E-mode (`candidate_observations`) IS
persisted via `adapter.write_observations` today and goes through the
existing oracle gate; only the new-risks / new-uncertainties side is
deferred.
