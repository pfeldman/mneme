# Phase 1 plan

**Summary.** Ship a small, opinionated knowledge-driven test runner (`praxis
regress` and `praxis explore`) on top of the Phase-0 core, plus the
regression-recall experiment that falsifies the moat. Rename `mneme` to
`praxis`. Do NOT activate states, paths, refuted status, or the Stagehand
adapter; defer Stagehand and the auditor protocol-as-input to Phase 1.5.

## Goal

Deliver Pablo's vision end-to-end against an extended `testapp.py`:

1. A user writes a goal once in natural language; `praxis learn` turns that
   into a seeded `*.knowledge.yaml` with success/failure signals + initial
   risks/uncertainties.
2. `praxis regress` runs before each deploy, regenerating steps from the
   believed knowledge and reporting pass/fail per goal.
3. `praxis explore` hunts off-happy-path for regressions, writing
   candidate observations the user reviews with `praxis review`.
4. The regression-recall experiment demonstrates that (memory R-mode +
   E-mode) catches more planted regressions than `cold_readme` at fixed
   token budget, with sigma-bounded kill criteria pre-registered.

## Scope

### In
- Rename `mneme` -> `praxis` (package, repo prose, schema $id, imports).
- Phase-1 schema activation for `risks` (with structured `trigger`) and
  `uncertainties` only. `success_signals` and `failure_signals` stay active
  (the latter promoted to load-bearing).
- `src/praxis/runner/` package: `regression.py`, `exploration.py`, `report.py`.
- CLI surface: `praxis init`, `praxis learn`, `praxis regress`, `praxis
  explore`, `praxis review`, `praxis status`.
- Extension of `experiments/ui-mutation/testapp.py` to support
  knowledge-visible regression categories (coupons, idempotency,
  permissions, paginated state) and a `/_plant` toggle for the planted
  regressions used by the experiment.
- Regression-recall experiment harness in `experiments/regression-recall/`:
  arms cold / cold_readme / memory, planted-regression manifest, metrics,
  kill gates with sigma bounds, pre-registration doc.
- Live subscription-path protocol (Claude Code as agent via Playwright MCP)
  documented as `experiments/regression-recall/LOCAL_RUN.md`.
- One paid API-key run on the regression-recall experiment to confirm
  tokens-dollars margin.
- ADR-0009 (this scope) and ADR-0010 (Phase 1 verdict, written after the
  live run).

### Out
- `states` and `paths` schema activation. Deferred to Phase 2.
- `refuted` status. Deferred to Phase 2; auditor protocol becomes offline
  oracle stress (see Phase 1.5).
- Stagehand adapter and the action-cache benchmark. Deferred to Phase 1.5
  with its own pre-registration.
- Multi-writer concurrency, MCP memory-server surface, dashboards, hosted
  store, secret redaction policies beyond the existing regex filter.
  Phase 2 / 3.
- A web UI. Markdown reports + JUnit XML are the surface.
- Selector recording / replay engine. Forbidden by AGENTS.md non-negotiable 1.
- Real-app (OSS SPA) validation. Phase 2 generalization; Phase 1 lands
  end-to-end on `testapp.py` first.

## Schema activation (precise)

Active fields and how the experiment consumes each:

| Field | Active? | Consumer | Note |
|-------|---------|----------|------|
| `success_signals` | yes | R-mode oracle | unchanged from Phase 0 |
| `failure_signals` | yes (promoted) | R-mode anti-goal; E-mode watch-list | regression = observed failure signal |
| `risks` (+ structured `trigger`) | yes (new) | E-mode probe targets | `trigger` validated at write time |
| `uncertainties` | yes (new) | E-mode + `praxis review` queue | author at any source_type |
| `states` | NO | n/a in Phase 1 | revisit if E-mode needs novelty |
| `paths` | NO | n/a in Phase 1 | regenerated each run |
| `refuted` status | NO | n/a in Phase 1 | auditor goes offline |

Schema lives at `schema/knowledge.schema.json` (the active one gets the
two new arrays; the reference `knowledge.phase1.schema.json` is dropped or
folded in). The `schema_version` stays `"0"` because the additions are
additive; old Phase-0 files validate unchanged.

`risks.trigger` validator at adapter boundary: structured form only. Two
accepted shapes (see ADR-0009 section 4). Free text rejected; borderline
cases produce an LLM-judge event in the store so the judgment is
traceable.

## Two modes

### R-mode (`praxis regress`)

```
Inputs:
  - believed success_signals + failure_signals for one or more goals
  - per-goal budget (tokens; actions fallback)
Outputs (per goal):
  - verdict: pass | fail | uncertain
  - observations written to the event log (source_type=agent)
  - JUnit XML + markdown report
Exit:
  - on first failure (configurable) or budget exhaustion
Auditor scenarios: NOT an input (see ADR-0009 section 6).
```

The runner depends only on `model`, `store`, `merge`, `oracle`, and an
injected `KnowledgeAdapter`. The adapter handles runtime details (browser
driving + agent prompting). The Phase-1 adapter is `praxis.adapters.
browser_use` extended with a `mode` flag; a future Stagehand adapter
plugs in without runner changes.

### E-mode (`praxis explore`)

```
Inputs:
  - believed + contested risks (with their structured triggers)
  - uncertainties
  - failure_signals (as anti-goals to watch for)
  - exploration budget (tokens; actions fallback)
Outputs:
  - candidate failure_signal observations (source_type=agent, status=contested)
  - new uncertainties (source_type=agent)
  - new candidate risks (source_type=agent, status=contested; trigger MUST
    pass the structured validator)
  - markdown digest summarizing candidates for review
Promotion:
  - existing diversity-or-seed gate (ADR-0005, ADR-0008). source_id =
    agent_identity (not run_uuid), so multi-run same-source repeats do not
    self-promote.
Observability floor:
  - off_path_fraction = (E-mode actions on URLs not visited by R-mode on
    the same goal) / (total E-mode actions). Logged per run; pre-registered
    floor for the experiment (see docs/phase-1-experiment.md).
```

## CLI surface

```
praxis init                    Bootstrap .praxis/ in cwd: config.yaml + knowledge/ + events/
praxis learn "<goal>"          Interactive seed: user describes success in natural language;
                                CLI compiles to *.knowledge.yaml (source_type=human).
                                Optional --risks adds risk authoring (structured trigger validated).
praxis regress [--goal <id>]   Run R-mode across known goals (or one). Writes events, prints
                                report, returns non-zero on any fail. --budget tokens|actions.
praxis explore [--budget T]    Run E-mode against believed knowledge; emits candidates.
praxis review                  Inspect contested/new observations from E-mode; accept
                                (writes a human-source-typed event) / quarantine / ignore.
praxis status                  Print believed knowledge summary per goal.
```

Console entry point declared in `pyproject.toml` `[project.scripts]`.
Implementation lives in `src/praxis/cli/`, depending only on the runner
package + click (added as a dep; ask Pablo before any other dep).

## Test app changes

`experiments/ui-mutation/testapp.py` extends to cover knowledge-visible
flows used by planted regressions. Net additions, no removal of the
existing login/search/checkout flows (Phase 0 evolution.py still must pass):

- Coupons: `/cart/apply` accepts coupon codes with subtotal predicates
  (e.g. `SAVE10` requires subtotal >= 50). The "SAVE10 at $49" edge is the
  canonical knowledge-visible regression target.
- Idempotency: `POST /orders` with the same `Idempotency-Key` must return
  the same `order_id`. A planted regression breaks this and creates
  double-orders.
- Permissions: a "settings" page visible only to admin users. A planted
  regression silently grants non-admin access.
- Paginated state: a list view with `?page=N` where a planted regression
  loses filter state across pages.

Planting via `/_plant?set=<NAME>` (toggle) and `/_planted` (introspect
ground truth). `/_reset` clears all planted regressions. No semantic
overlap with Phase-0 mutations (those toggle UI shape, not behavior).

## Deliverables (ordered by dependency)

1. **Branch + design docs committed** (ADR-0009, this plan,
   `phase-1-experiment.md`). No code yet.
2. **Rename `mneme` -> `praxis`** in one commit. `verify.sh` stays green
   after. (Repo rename on GitHub is a manual step Pablo handles.)
3. **Schema activation**: add `risks` (with structured trigger validator)
   and `uncertainties` arrays. Pydantic model mirrors them. `test_model_
   schema_agree.py` updated. Schema examples updated.
4. **R-mode runner** (`src/praxis/runner/regression.py`). Tests against
   simapp. `praxis regress` CLI wired.
5. **E-mode runner** (`src/praxis/runner/exploration.py`). Tests against
   simapp. `praxis explore` CLI wired. `off_path_fraction` metric
   instrumented.
6. **Testapp extensions** (coupons, idempotency, permissions, paginated
   state, `/_plant` API). Tests for the toggles.
7. **Planted-regression manifest** (`experiments/regression-recall/
   manifest.json`): N=8 regressions across 4 categories, ground truth.
8. **Regression-recall harness**: arms wiring, metric computation, kill
   gates with sigma bounds, JSON + markdown output.
9. **Live subscription-path protocol** (`experiments/regression-recall/
   LOCAL_RUN.md`): step-by-step for Claude Code as agent via Playwright
   MCP. API-key path documented as alternative.
10. **`praxis init` + `learn` + `review` + `status`**: lower-priority CLI
    commands, last because they are user-facing polish, not load-bearing
    for the experiment.
11. **One paid API-key run** of the regression-recall experiment to confirm
    the margin survives in tokens-dollars (Pablo configures the key).
12. **ADR-0010**: Phase 1 verdict from the live run.

## Open risks (Phase 1 specific)

- **Cold_readme strawman risk** (adversarial concern 8). Mitigation: the
  README used by `cold_readme` is committed to the repo with its git sha
  pre-registered in the experiment manifest, before any arm runs. An
  independent reviewer (Pablo role-playing the cold-arm advocate)
  signs off that the README is the strongest case for the cold arm
  before runs begin.
- **E-mode prompt overfit** (adversarial concern 11). Mitigation:
  `off_path_fraction` floor pre-registered as a hard kill (not a tuning
  knob); the E-mode prompt is version-pinned per run, changes invalidate
  prior data.
- **Trigger validator escapes** (adversarial concern 9). Mitigation: the
  validator is a small dedicated module with unit tests covering banned
  phrasings; borderline cases emit an LLM-judge event so the call is
  traceable.
- **Subscription metrics drift** (Phase 0 caveat). Mitigation: log actions
  + wall time + tokens (when API path is used) on every run. The kill
  criterion is primary in tokens; secondary in actions for cross-checks.
- **Scope creep**. Mitigation: the schema-activation table above is the
  contract. Adding a field requires an ADR; the burden of proof is
  "named experiment consumer".

## Out of scope, documented explicitly

The auditor protocol survives as an OFFLINE oracle-stress check
(`experiments/auditor/`, runs analogously to `oracle_stress.py`),
producing `contested` events through the existing diversity-or-seed gate,
NOT a new `refuted` status. The Stagehand benchmark is a separate Phase
1.5 deliverable with its own pre-registered prompts.
