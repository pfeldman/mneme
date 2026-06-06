# Experiment: the decisive UI-mutation test

KILL or VALIDATE the thesis before building the product. Build this FIRST.

## Claim
Goal+knowledge step regeneration is (1) cheaper / more reliable than a COLD
agent, and (2) more robust than a RECORDED script when the UI changes.

## Arms
- `memory` — agent reads believed knowledge and regenerates its own steps.
- `cold_agent` — same agent, no memory, figures it out each run.
- `recorded_script` — a Playwright script captured once (the brittle baseline).

## Setup
- Runtime: Browser Use. One writer. Flows: login, search, checkout.
- Minimal Phase-0 schema (`schema/knowledge.schema.json`).
- **Seed each goal's success oracle (human/spec) before exploring** (ADR-0005).
- Memory run 1: explore, populate knowledge. Run 2+: achieve the goal USING
  knowledge, regenerating steps.

## Measure (ORDER MATTERS)
1. **Existential gate — `memory` vs `cold_agent`:** tokens + wall time + success
   rate, no mutation. If cold wins or ties on cost at equal reliability, STOP.
2. **Robustness — after a mutation (`mutate.py`):** `memory` vs `recorded_script`
   recovery rate.
3. **Guardrail — oracle correctness:** false-pass / false-fail across all runs.

## Files
- `metrics.py` — `RunResult` + the gates + report writer.
- `harness.py` — runs the arms; checks the existential gate FIRST and short-circuits.
- `mutate.py` — UI mutation injector (rename control, move field, swap
  email→username, insert step). Each mutation changes HOW, never WHETHER.

## Kill criterion
Stop unless `memory` clears all three: cheaper-or-equal vs cold at equal
reliability, more robust vs the recorded script, and oracle false-pass below
brittle-test levels.

## Files (implemented)
- `metrics.py`   — `RunResult` + the three gates + verdict + report writers.
- `harness.py`   — runs the arms, checks the existential gate FIRST, short-circuits.
- `mutate.py`    — the four UI mutations (rename / move / swap / insert).
- `simapp.py`    — a deterministic, in-process stand-in for the SUT + the three arms.
- `runtimes.py`  — wires the arms through the REAL core (store→merge→oracle→adapter).

## How to run
```bash
python experiments/ui-mutation/harness.py     # prints the three gates + verdict
pytest tests/test_experiment_harness.py       # asserts the gate machinery
```
Outputs `results.json` (per-run) and `results.md` (summary).

## ⚠️ What these numbers are (and are NOT)
A *live* existential gate needs Browser Use + an LLM + a real SUT — none of which
run in CI/sandbox. So the harness runs against `simapp`, a deterministic stand-in.
Its token magnitudes are **explicit assumptions encoding the thesis premise**
(recognition via a remembered oracle is cheaper than re-deriving it cold), **not
measurements**. The sim therefore validates the *machinery* — the gate ordering,
the metrics, the diversity-or-seed oracle, the mutation flow, and the kill/continue
logic — end-to-end. It does **not** by itself validate the thesis.

What IS real in the sim path: the append-only store, the believed projection, and
the oracle's diversity-or-seed rule (the actual production code). What is modeled:
the SUT and the agent's token cost.

## Wiring the live arm (to get empirical numbers)
1. `pip install -e ".[dev,browser-use]"` and set an LLM key.
2. Stand up a real test app (or a hosted target) with the login/search/checkout
   flows; implement `mutate.apply/reset` against it (proxy / DOM patch / feature
   flag) instead of the in-process `simapp` state.
3. Replace `simapp.run_memory/run_cold/run_recorded` with calls that drive
   `BrowserUseAdapter` (memory/cold) and an emitted Playwright script
   (`adapters.playwright.RecordedScript`, recorded baseline), measuring real tokens
   and wall time.
4. Re-run `harness.py`. The gate/verdict logic is unchanged; only the runtime swaps.
