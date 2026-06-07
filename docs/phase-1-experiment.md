# Phase 1 experiment: the regression-recall falsifier

This doc is the PRE-REGISTRATION. Everything stated here is fixed before
any arm runs. Deviations after that point invalidate the run and require
a new pre-registration ADR.

## Hypothesis

At a fixed token budget per goal per release, an agent equipped with the
believed knowledge of the system under test (success/failure signals +
risks + uncertainties) catches strictly more planted regressions than a
strong cold agent primed with the public README, with the per-category
edge concentrated on regressions a stranger to the app would not probe.

Falsifying form: under the kill criteria below, the moat does NOT exist.
Phase 1 returns to the kill/continue decision in docs/04.

## Arms

| Arm | Knowledge given to the agent | Notes |
|-----|------------------------------|-------|
| `cold` | goal string + base URL only | Phase-0-equivalent baseline. Kept as a reference; not the kill-criterion target. |
| `cold_readme` | goal string + base URL + frozen public README + one sentence per goal | The steelman. The README is committed to git with its sha in the manifest BEFORE any arm runs. The per-goal sentences are written by an independent reviewer (Pablo role-playing the cold-arm advocate) without access to the seeded knowledge file. |
| `memory` | goal string + base URL + believed Phase-1 knowledge for the goal (success/failure signals, risks, uncertainties) | Runs R-mode + E-mode. The R-mode pass uses success/failure signals; the E-mode pass uses risks/uncertainties. Both share the same budget. |

Auditor scenarios are NOT an input to any arm in this experiment (see
ADR-0009 section 6). They run as an offline oracle-stress check in a
separate harness.

Stagehand (action-cache benchmark) is NOT included in this experiment.
It is a separate Phase 1.5 deliverable with its own pre-registration.
Half-committing here is forbidden (adversarial concern 12).

## Test app

`experiments/ui-mutation/testapp.py` extended per the plan: existing
login / search / checkout flows preserved, plus coupons, idempotency,
permissions, paginated state. Total 6 goals covered.

The frozen README (`experiments/regression-recall/README_FROZEN.md`) is
committed to git at sha `<TBD-pre-run>` before any arm runs. Its content
documents the testapp surface accurately, written without reference to
planted regressions. The git sha is recorded in the run manifest.

## Planted regressions

N=8 planted regressions in 4 categories. Distribution chosen to test
both the moat (knowledge-visible categories) and the negative (memory
must NOT beat cold on tourist-visible categories, or the experiment is
exposing a different bias).

| # | Category | Slug | Description (ground truth) | Visibility |
|---|----------|------|----------------------------|-----------|
| 1 | tourist | `t1_login_500` | `/login` returns 500 on any POST | Both arms should catch trivially |
| 2 | tourist | `t2_search_blank` | `/search?q=X` returns blank page | Both arms should catch trivially |
| 3 | coupons | `k1_save10_at_49` | `SAVE10` accepted with subtotal=49 (should require >= 50) | Knowledge-visible (risk authored from a prior release) |
| 4 | coupons | `k2_stack_codes` | Two coupons stack in same cart | Knowledge-visible (uncertainty filed from a prior release) |
| 5 | idempotency | `k3_double_order` | `POST /orders` with same Idempotency-Key creates two orders | Knowledge-visible (risk authored) |
| 6 | permissions | `k4_admin_settings` | Non-admin user reaches `/settings/admin` via direct URL | Knowledge-visible (risk authored) |
| 7 | paginated | `k5_filter_lost` | `?page=2` loses filter applied on page 1 | Knowledge-visible (uncertainty filed) |
| 8 | stale-trap | `s1_oracle_lies` | Success signal "POST /session returns 200" still fires but the session is invalid | Stale-trap: memory must NOT trust the believed oracle blindly; this tests the oracle's stale demotion |

Ground truth is in `experiments/regression-recall/manifest.json`. The
manifest is committed BEFORE any arm runs and immutable for the duration
of the experiment.

The planter is a separate session from the seed author. Wash-out protocol:
- 72 hours minimum between authoring the knowledge file and authoring the
  planted regression for each category.
- Planter does not read the seeded knowledge before committing the
  manifest.
- Leakage check: lexical overlap between any seeded risk/uncertainty
  free-text and the corresponding manifest entry MUST be below 30%
  (computed as Jaccard on bag-of-words after lowercasing + stopword
  removal). Exceeding this threshold invalidates the run.

## Metric

### Primary

`recall@budget`: fraction of the 8 planted regressions detected by the
arm within the fixed token budget B. Detected = the arm writes an
observation whose value matches the manifest's `expected_observation`
field (Jaccard >= 0.6 with the canonical phrasing; ties broken by an
LLM-judge event that is logged).

### Secondary

- Per-category recall: `recall@budget` restricted to {tourist},
  {coupons + idempotency + permissions + paginated}, {stale-trap}.
- False-positive rate per arm: arm claims a regression that is NOT in
  the manifest. Adjudication: an LLM-judge event with a pinned prompt,
  graded against the testapp source. False positives are logged
  separately from misses.
- `off_path_fraction` (memory arm only): fraction of E-mode actions on
  URLs not visited by R-mode on the same goal. Logged per run.

### Cost unit

Tokens are primary (Pablo's existential framing in docs/06). Actions and
wall time are logged as secondary cross-checks.

The token budget B is calibrated against memory's R-mode happy-path cost
on a CLEAN release (no planted regressions), targeting 95% utilization.
Calibrated BEFORE the manifest is committed. Calibrating against the
cold arm (the synthesis draft's approach) gives memory a free E-mode
lap; calibrating against memory's R-mode equalizes (adversarial concern 5).

If memory's R-mode + E-mode exceeds B on a planted release, E-mode is
truncated. This is the contract: all arms get B tokens, all arms must
fit their behavior within B.

## Kill criteria (sigma-bounded, pre-registered)

The moat does NOT exist if ANY of:

1. `recall(memory) - recall(cold_readme) < 0.15` absolute, OR the
   difference is within 2 sigma of inter-seed variance on either arm.
2. On combined knowledge-visible categories (coupons + idempotency +
   permissions + paginated): `recall(memory) - recall(cold_readme) <
   0.25` AND the difference within 2 sigma of inter-seed variance.
3. `false_positive_rate(memory) > false_positive_rate(cold_readme) +
   0.05` AND the difference is more than 1 sigma (memory buys recall
   with false alarms beyond what cold does).
4. `false_pass_rate(memory) > 0.0` on the unmutated control release
   (Phase 0 guardrail regresses).
5. `recall(memory) on s1_oracle_lies < 0.5` (oracle stale demotion fails
   under live load: memory trusts a believed oracle blindly when the
   underlying behavior changed).
6. `off_path_fraction(memory) < 0.4` averaged across E-mode runs
   (E-mode degenerated into R-mode; the moat result is structurally
   invalid regardless of recall value).

The disjunction matters: any single failure kills. The conjunction
"memory wins overall AND on knowledge-visible AND on stale-trap AND on
off-path-fraction AND not via false alarms" is the moat. Anything less
returns the project to the kill/continue gate.

The 2-sigma bound is a Welch's t-test approximation, not an exact test;
the run reports both the raw delta and the t-statistic so the call is
auditable.

## Sample size and variance

- 3 arms x 6 goals x 1 release = 18 base run-configurations.
- Each run-configuration repeated 5 times with different LLM seeds = 90
  agent runs total.
- Inter-seed variance computed per arm per category.
- All seeds and run manifests committed to the repo after the
  experiment completes (immutable record).

## Threats to validity (and mitigations)

1. **Leakage between planter and seed author.** Mitigated by the 72-hour
   wash-out + lexical-overlap floor check (manifest invalidated if
   overlap exceeds 30%).
2. **Cold_readme strawman.** Mitigated by freezing the README via git
   sha before any arm runs + independent reviewer signoff on the
   per-goal sentences.
3. **Budget calibration favoring memory.** Mitigated by calibrating
   against memory's R-mode happy-path cost on a clean release, not
   against cold (adversarial concern 5).
4. **E-mode degenerating into R-mode (prompt overfit).** Mitigated by
   pre-registered `off_path_fraction` floor as a hard kill criterion.
5. **LLM-judge bias on false-positive adjudication.** Mitigated by
   pinning the judge prompt, logging the judgment as an event in the
   store (traceable, not silent), and committing the judge prompt sha
   in the manifest. A 10% sample of judgments is re-graded by a second
   LLM-judge family (run after the experiment completes); high
   disagreement invalidates the run.
6. **Stale-trap visibility.** Mitigated by including `s1_oracle_lies` as
   a category-of-one: it is the only test of stale demotion and has its
   own kill criterion (5).

## Pre-registration commits (in order, BEFORE any arm runs)

1. `experiments/regression-recall/README_FROZEN.md` (the steelman
   README for cold_readme).
2. `experiments/regression-recall/cold_readme_per_goal.md` (one
   sentence per goal, independent reviewer authored).
3. `experiments/regression-recall/manifest.json` (the 8 planted
   regressions with `expected_observation` fields, sealed).
4. `experiments/regression-recall/budget.json` (calibrated token budget
   B from a clean-release memory R-mode pilot).
5. `experiments/regression-recall/judge_prompt.txt` (the false-positive
   adjudication prompt, sha recorded).
6. `experiments/regression-recall/emode_prompt.txt` (the E-mode prompt,
   sha recorded).

After arms run:

7. `experiments/regression-recall/results.json` (per-run records).
8. `experiments/regression-recall/results.md` (summary + verdict).
9. `docs/adr/0010-phase-1-verdict.md` (continue / kill).
