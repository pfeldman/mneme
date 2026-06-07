# Handoff prompt - Praxis (formerly Mneme), end of Phase 1

Copy this entire document into the next Claude Code session (or a fresh
agent) as the opening prompt. It carries the smallest complete brief
needed to pick up the work without re-reading every file. It is written
to be self-contained for a cold reader.

---

## What Praxis is

A shared **operational knowledge** layer for QA agents: success / failure
oracles, risks, uncertainties, all carrying provenance + confidence +
status, decoupled from any procedure. Agents read believed knowledge,
regenerate their own steps, and write observations back through an
append-only event log. The merge projection folds events into believed
state via the oracle's diversity-or-seed rule (ADR-0005, ADR-0008): a
single source cannot self-corroborate; only a seeded human/spec OR two
independent sources with diverse evidence types reach `believed`.

The project was originally called `mneme`; ADR-0009 renamed it to
`praxis` (Aristotelian "practical wisdom applied to a particular
situation"). The repo URL is still `github.com/pfeldman/mneme` because
the GitHub rename is a manual step Pablo will do separately.

## Where you are landing

- Repo: clone `github.com/pfeldman/mneme`. Branch:
  `claude/mneme-phase-1` (NOT main; do not switch).
- Working tree: `cd mneme; bash verify.sh` must end ALL GREEN before
  any new work. 144 tests, 6 sigma-bounded kill gates, offline harness,
  oracle adversarial stress, evolution-over-time test.
- Last commits (newest first): rename mneme->praxis, schema activation
  for risks+uncertainties+structured triggers, R-mode + E-mode
  runners, testapp with 7 planted regressions, regression-recall
  metrics + manifest + sigma-bound gates, pre-registration artifacts
  (frozen README, judge prompt, etc), LOCAL_RUN protocol, CLI, adv
  fixes, dry-run fixes (/me + /_reset_state + 3-goal default plan).

## The contract you must honor

`AGENTS.md` has the five non-negotiables. Read it first. Summary:

1. Invariants, not coordinates. No selectors as durable knowledge.
2. Provenance + confidence are mandatory on every assertion.
3. Append-only store; believed state is a projection, never
   last-write-wins.
4. Runtime-agnostic core: `model / store / merge / oracle / runner`
   have zero browser deps.
5. The oracle is sacred. A single source never self-corroborates.

Plus, per `docs/06-risks-and-failure-modes.md`: a confidently-wrong
oracle is worse than no memory. Brittleness is loud; bad knowledge is
silent. When in doubt, choose the option that makes a wrong assertion
LOUD and TRACEABLE over silent and convenient.

## Where Phase 1 stopped (state of the world)

**Done:**
- Design docs sealed (ADR-0009, `docs/phase-1-plan.md`,
  `docs/phase-1-experiment.md`).
- Praxis core (model + store + merge + oracle + runner + CLI).
- Phase 1 schema active: `risks` (HTTP/sequence structured triggers,
  banned-phrase validator in `src/praxis/model/trigger_validator.py`),
  `uncertainties`. `states` + `paths` + `refuted` deferred.
- R-mode + E-mode runners producing pass/fail/uncertain verdicts and
  candidate observations. Off_path_fraction observability for E-mode.
- testapp with 7 planted regressions reachable via /_plant + /me +
  /_reset_state.
- Regression-recall harness with 3-arm plan (cold, cold_readme,
  memory[R+E]) x 5 seeds x 3 goals = 45 runs.
- Six sigma-bounded kill gates in `experiments/regression_recall/
  metrics.evaluate`.
- Pre-registration artifacts: frozen README (with cold_readme leak
  fix), per-goal sentences, judge prompt, pre-registration manifest.
  `prompts.py` sha pins the E-mode prompt.
- Seeded knowledge for 3 goals (`experiments/regression_recall/
  knowledge/login|search|checkout|admin_access.knowledge.yaml`).
- End-to-end dry-run validated with synthetic-but-realistic executors
  against the live testapp; verdict CONTINUE with all 6 gates PASS on
  synthetic data.
- 144 tests; verify.sh ALL GREEN.

**Not done (and why):**

1. **The actual live LLM-driven regression-recall run has NOT
   happened.** The previous agent could not run 45 LLM browser
   sessions in one context. The next step is Pablo running
   `experiments/regression_recall/LOCAL_RUN.md` (subscription path
   with Claude Code + Playwright MCP, or API-key path with multi-
   model).
2. **The independent reviewer signoff on the cold_readme arm has NOT
   been done.** `README_FROZEN.md` + `cold_readme_per_goal.md` were
   authored by the same session that wrote the manifest. The
   pre-flight checklist in LOCAL_RUN.md requires a separately-isolated
   reviewer to re-author or endorse them before any arm runs.
3. **t1_login_500 and s1_oracle_lies cannot be planted in the same
   release.** t1 makes /session 500 before s1's cookie trap fires.
   The dry-run plants 7 regressions (skipping t1). This is documented
   as a release-design constraint, not a code bug. Phase 1 ships ONE
   release (`phase-1-r1`) without t1; a Phase 1.5 release can include
   t1 alone.
4. **API-key path is documented but no executor implementation
   exists** in the repo for multi-model comparison. The subscription
   path uses paste-back; the API-key path (anthropic / openrouter)
   needs an Executor implementation that wraps the chosen LLM.

## What to do next (priority order)

If Pablo asks "what next", default to this sequence:

1. **Run the live experiment.** Walk `LOCAL_RUN.md` step by step.
   Independent-reviewer signoff on cold_readme. Pin all shas in the
   run manifest. Execute arms cold -> cold_readme -> memory back to
   back per release. Run on phase-1-r1 (7 plants, no t1) and on a
   control release (no plants). Compute verdict via `report()`.
2. **Record the verdict in ADR-0010** (`docs/adr/0010-phase-1-
   verdict.md`). If `kill`, the project returns to the kill/continue
   gate in `docs/04-mvp-experiment.md` and STOPS. If `continue`,
   Phase 1.5 + Phase 2 are unblocked.
3. **Phase 1.5** (`docs/phase-2-plan.md` cross-refs this): Stagehand
   adapter + dedicated benchmark, auditor offline harness with refuted
   status (now under proper diversity rule), one paid API-key run
   confirming tokens/$ margin. None of these blocks Phase 2.
4. **Phase 2** (`docs/phase-2-plan.md` is the draft): multi-writer
   concurrency, real-app generalization, exploration incentive,
   E-mode candidate persistence. Each gets its own ADR before
   implementation.

## How Pablo and you communicate

- Espanol en el chat. Mensajes cortos. Una idea por turno. No fabular
  numeros. No hacer commits sin "commit / dale / mandale" explicito.
- Codigo y commits en ingles. ASCII puro: ni em-dashes, ni smart
  quotes, ni Unicode flechas / ellipsis. El hook
  `~/.claude/hooks/block-em-dashes.py` defenestra commits que tengan
  esos.
- Cada decision estructural -> nuevo ADR en `docs/adr/`.
- Verify.sh debe quedar verde en cada commit. Pre-existing tests no
  pueden romperse para acomodar nuevos.
- Nunca downgradear los cinco non-negotiables sin un ADR explicito
  que lo justifique. La sangre del proyecto es que un wrong assertion
  sea LOUD y TRACEABLE; cualquier cambio que mueva eso a SILENT y
  CONVENIENT es no-go.

## Files that have the rest of the truth

Read in this order if anything above is ambiguous:

1. `AGENTS.md` (the contract).
2. `docs/01-vision-and-thesis.md` (what we are building and why).
3. `docs/06-risks-and-failure-modes.md` (where this goes wrong).
4. `docs/phase-0-results.md` (what was actually measured in Phase 0).
5. `docs/adr/0009-phase-1-scope-and-praxis-reframe.md` (the reframe
   and the Phase 1 scope cuts).
6. `docs/phase-1-plan.md` + `docs/phase-1-experiment.md` (the
   falsifier + how to run it).
7. `experiments/regression_recall/LOCAL_RUN.md` (operator protocol).
8. `experiments/regression_recall/pre_registration.md` (sealed
   artifacts contract).
9. `docs/phase-2-plan.md` (the next-phase scoping draft).
10. The ADR README at `docs/adr/README.md` lists all numbered
    decisions in order; the most recent are 0007 (Phase 0 cleared),
    0008 (oracle source independence), 0009 (Phase 1 scope +
    rename).

## Useful commands

```bash
# Run everything end-to-end (must be ALL GREEN):
bash verify.sh

# Just the unit tests (fast):
source .venv/bin/activate && python -m pytest -q

# Start the test app on a free port:
source .venv/bin/activate && python experiments/ui-mutation/testapp.py --port 8765

# Plant + verify a single regression:
curl -s 'http://127.0.0.1:8765/_plant?set=k1_save10_at_49'
curl -s 'http://127.0.0.1:8765/_planted'

# Use the CLI on a real project:
praxis init --app my-saas --base-url https://staging.example.com
praxis learn login --from-file login.knowledge.yaml
praxis regress --goal login --budget-tokens 5000
praxis explore --goal checkout --happy-path /cart /cart/apply /orders
praxis status

# Run the regression-recall harness offline (executor is a callable):
python -c "
import sys; sys.path[:0] = ['src', 'experiments']
from regression_recall.harness import build_default_plan, run_plan, report
plan = build_default_plan(n_seeds=3, budget_tokens_per_goal=2000)
records = run_plan(plan, lambda *a, **k: {'observations':[],'actions_used':0,
                    'tokens_used':0,'off_path_fraction':None,'visited_urls':[]},
                   base_url='http://127.0.0.1:8765')
print(report(records, plan))
"
```

## Acknowledgements + caveats the next agent should carry

- The Phase 1 dry-run was honest about its limits: synthetic
  observations, not LLM observations. Do NOT report "we ran Phase 1"
  to Pablo; the live falsifier is still pending his execution.
- The adversarial review (HAS-BLOCKERS) caught a real cold_readme
  leak that was fixed; a second independent review is still
  recommended before any moat claim is published.
- The `_value_matches` Jaccard floor in `regression.py` (0.5) is
  DELIBERATELY DIFFERENT from `PARAPHRASE_FLOOR` in `metrics.py` (0.6).
  Runner is lenient (product surface); experiment matcher is strict
  (pre-registered falsifier). Don't unify them without an ADR.
- Branch `claude/mneme-phase-1` is the source of truth for Phase 1.
  Do NOT merge to main until ADR-0010 is written and Pablo signs off.

Good luck. Read `AGENTS.md` first.
