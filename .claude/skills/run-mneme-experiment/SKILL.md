---
name: run-mneme-experiment
description: Run the Mneme UI-mutation experiment LIVE using Claude Code as the browser agent (via a Playwright MCP) against the local test app. Use when the user wants to execute the live existential/robustness gates with their Claude Code subscription (no API key, no local model). For the offline machinery check, run `bash verify.sh` instead.
---

# Run the Mneme live experiment (Claude Code is the agent)

You drive the experiment yourself using the Playwright MCP `browser_*` tools. The
runtime is THIS Claude Code session (= the user's subscription); Browser Use is not
used. Read `experiments/ui-mutation/LOCAL_RUN.md` for full detail; the protocol is
summarized below. Measurement ORDER is the whole point — never reorder it.

## Preflight
1. Confirm a Playwright MCP is connected (`/mcp` shows `browser_*` tools). If not,
   tell the user to run `claude mcp add playwright -- npx -y @playwright/mcp@latest`
   and stop.
2. Start the test app in the background:
   `python experiments/ui-mutation/testapp.py --port 8000`
   Flows: http://127.0.0.1:8000/login /search /cart.
   Mutations: `GET /_mutate?set=NAME`, `GET /_reset`, `GET /_state`.
3. Build a `mneme.adapters.BrowserUseAdapter` over a fresh `FileEventStore`, seeded
   for all three flows (use `experiments/ui-mutation/runtimes.py:make_adapter`).
   `GET /_reset` first.

## Cost metric & repetitions
The subscription hides per-task tokens, so record `RunResult.actions` (count every
`browser_*` action you take) plus `wall_seconds` (time each run). `metrics`
falls back to actions automatically (`cost_unit` says which).

LLM runs are NON-DETERMINISTIC, so a single run hides the spread that decides
whether the edge is real. Run **each arm ≥5 times per flow** for M1, time every run,
and report `metrics.summarize_arm(...)` (mean ± stdev, min/max, success rate) — not
just the average. A cost edge inside one stdev of noise is not an edge.

## Protocol (do in this order)
1. **Population** (memory arm, unmutated): for each flow achieve the goal, then
   `adapter.write_observations(...)` the success signals you actually observed —
   a behavioral one (Sign out / results list / order-confirmation element) AND a
   network one (the 2xx on /session, /search, /order). Two evidence types give the
   oracle its diversity on top of the seed.
2. **Measurement 1 — EXISTENTIAL GATE (FIRST, unmutated). ≥5 reps/arm/flow.**
   - `memory`: read believed knowledge (`adapter.knowledge_to_prompt(kf)`), achieve
     each goal, count actions, time it; `oracle = runtimes.oracle_said_success(...)`.
   - `cold_agent`: same flows, ignore all prior knowledge, figure it out cold.
   - `gate1 = metrics.existential_gate(memory, cold)` (now carries per-arm
     mean±stdev and wall time).
   - **If `gate1["PASSED"]` is False, OR memory's cost edge is within one stdev of
     cold → STOP, report the numbers, do not continue.**
3. **Measurement 2 — ROBUSTNESS** (only if M1 passed): for each mutation, `_mutate`
   it, and for each flow it perturbs run the `memory` arm (regenerate from knowledge)
   and the `recorded_script` baseline (`experiments/ui-mutation/LOCAL_RUN.md` §6 —
   it should break). `_reset` after each. `gate2 = metrics.robustness_gate(...)`.
4. **Guardrail**: include a few runs where the goal is NOT reached; confirm the
   oracle never reports success. `metrics.oracle_error_rates(...)`.
5. **Verdict**: `metrics.verdict(gate1, gate2, oracle)`, then
   `metrics.write_markdown(...)` and `metrics.write_report(...)`.

## Report back
Print the three gates and the CONTINUE/STOP verdict, and state plainly that these
are EMPIRICAL (real browser + real LLM + local app), unlike the simulator numbers
from `verify.sh`. Stop the test app when done.
