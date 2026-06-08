# Phase 2 plan (draft)

**Pre-condition.** Phase 1 has shipped a complete, falsifiable harness
(branch `claude/mneme-phase-1`, 144 tests green, verify.sh ALL GREEN).
The Phase-1 LIVE run via `LOCAL_RUN.md` has NOT happened yet; Phase 2
work below should not begin until the Phase-1 verdict is recorded in
ADR-0010 (continue or kill). Pure planning here, not commits.

## What Phase 2 is for

Phase 1 validated one writer against a controlled SUT. Phase 2 adds the
load-bearing parts that make this an actual product, not a proof of
concept:

1. **Multi-writer concurrency.** Two or more agents writing into the
   same store; contradiction surfaced as `contested`; oscillation as
   `quarantined`; recency decay so old beliefs do not silently outvote
   new evidence.
2. **Exploration incentive.** Resolve the docs/05 coverage-collapse
   risk: agents converge on the happy path, tests go blind. Reward
   agents for resolving `uncertainties` and discovering new `paths`,
   not just for re-achieving goals.
3. **Real-app generalization.** Move off `testapp.py`. Pick one OSS
   SPA (candidates: Saleor, Conduit, OpenMRS) as the second SUT and
   re-run the regression-recall experiment with multi-writer seeds.
4. **Auditor protocol as offline oracle-stress.** ADR-0008-style
   adversarial test where a separately-isolated session writes
   known-failure scenarios; the oracle must NOT promote a believed
   signal that passes a known-broken scenario. Promotion to a new
   `refuted` status only if diversity holds (two independent failure
   detectors).
5. **Stagehand adapter + benchmark.** Implement the second
   `KnowledgeAdapter` and run the head-to-head regression-recall
   against its action cache. This is the moat-vs-procedural-cache
   experiment the docs/06 existential risk specifically calls out.
6. **E-mode candidate persistence.** Phase 1 emits new risks /
   uncertainties as a typed result but does NOT persist them. Phase 2
   extends the store event model so `praxis review` can promote
   contested candidates across sessions.

## Deliverables (ordered by dependency)

1. **ADR-0010** records the Phase 1 verdict (or kills the project).
   Phase 2 STARTS HERE; if Phase 1 killed, none of the below ships.
2. **ADR-0011** scopes Phase 2 (mirror of ADR-0009): activates which
   fields, names which experiments, defers which work.
3. **Multi-writer store + projection tests** (docs/05). Concurrent
   appends must not lose events; contested vs quarantined paths
   exercised under parallel writers.
4. **Stagehand adapter** (`src/praxis/adapters/stagehand.py`) + a
   pre-registered regression-recall experiment that pits Praxis memory
   vs Stagehand's action cache on the same SUT.
5. **Real-app generalization**: port the Phase 1 manifest +
   regression-recall machinery to one OSS SPA. New
   `experiments/regression_recall_real/`.
6. **Auditor offline harness**
   (`experiments/auditor/`, analog of `oracle_stress.py`): adversarial
   "known-broken scenario" injection + `refuted` status promotion via
   the diversity-or-seed gate.
7. **Exploration incentive**: a budget-weighted reward function that
   credits an E-mode run by uncertainties resolved + new paths
   discovered, not just by goals re-achieved. Pre-registered in an
   ADR before any experiment relies on it.
8. **E-mode candidate persistence**: extend
   `praxis.store.events.ObservationEvent` (or add a sibling event type)
   to carry candidate risks / uncertainties so `praxis review`
   surfaces them.

## Out of scope for Phase 2 (deferred to Phase 3)

- Governance, RBAC, hosted shared memory, dashboards.
- Secret redaction beyond the current adapter-boundary regex.
- Poisoning detection beyond the existing diversity-or-seed gate.
- A web UI. CLI + markdown reports continue.
- Pricing / GTM. The moat is technical first.

## Open risks (Phase 2 specific)

- **Multi-writer correctness is the hardest module.** Get it wrong and
  poisoning becomes silent (docs/06). Adversarial harness from day one,
  not as a post-hoc check.
- **Real-app porting may surface latent assumptions** baked into
  testapp.py. Expect the Phase 2 schema to need a small additive
  extension (likely around session / auth-state representation).
- **Stagehand benchmark may show ties** on some flow categories. The
  Phase 1 reframe (operational knowledge, not memory) implies Praxis
  wins on *what to test*, not *how to drive the browser*; if the
  Stagehand comparison reduces to "who drives faster", we measured the
  wrong axis. Pre-register the test categories accordingly.
- **Exploration incentive overfits to the metric.** Reward functions
  invite Goodharting. Mitigate with adversarial review of the reward
  function before any agent optimizes for it; alternate metrics
  (random-walk baseline) on the first Phase 2 experiment.
