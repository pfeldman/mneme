# ADR-0007: Phase-0 existential gate cleared (provisionally) — proceed to Phase 1

Status: Accepted

## Context
docs/04 defines a kill/continue gate: the layer must (M1) be cheaper than a cold
agent at equal reliability, (M2) be more robust than a recorded script under UI
mutation, and (guardrail) keep oracle false-pass below brittle-test levels. The
project does not proceed past Phase 0 until all three clear with real numbers.

A first LIVE run (2026-06-06; Claude Code as the browser agent via Playwright MCP
against the local test app) produced, in browser-action cost:
- M1: memory 4.67 vs cold 8.33 (ratio 0.56), both 100% success → PASS.
- M2: memory recovery 6/6, recorded script 1/6 → PASS.
- Guardrail: false_pass 0.0, false_fail 0.0 (n=20) → PASS.
Full numbers and caveats: docs/phase-0-results.md.

## Decision
Treat the Phase-0 existential gate as **cleared provisionally** and proceed to
Phase 1 (harden model/store/merge, activate the richer schema, add a second
adapter + benchmark). "Provisionally" is load-bearing: the result is a single run
with small samples and an action-based cost proxy, so Phase 1 work must NOT assume
the margin is settled.

Before relying on the gate as settled, the following must be done (tracked as
Phase-1 entry work):
1. Repetitions + variance: each arm ≥5× per flow; report mean ± stdev and wall
   time; a cost edge within one stdev of noise does not count.
2. Cost in tokens/$ and wall time, not only actions, on at least one API-key run.
3. Adversarial oracle stress (broken app, poisoned/contradictory observations,
   stale versions) to confirm false_pass stays ~0.

## Consequences
+ Phase 1 may start; the thesis has a measurable, real margin (not refuted).
+ The bar and its caveats are recorded, so "we passed Phase 0" cannot quietly
  inflate a single small run into a settled fact.
- If the rigorous re-run (items 1–3) erases the margin or raises false_pass, this
  ADR is superseded and the project returns to the kill/continue decision.

## Update — 2026-06-07 (rigorous re-run)
A multi-rep live run (M1 n=15/arm, M2 n=18/arm, 68 runs total; docs/phase-0-results.md)
settles item 1: M1 cost edge is memory 4.667 ± 0.471 vs cold 8.333 ± 0.943 actions —
a 3.67-action margin against a 0.94 max stdev (~3.9σ), corroborated by wall time
(31.2 s vs 51.3 s). M2 recovery 18/18 vs 3/18. Guardrail false_pass 0 at n=68.

- ✅ Item 1 (reps + variance): DONE. Cost/robustness axes are statistically settled.
- ⏳ Item 2 (tokens/$ on an API-key run): still open — actions and wall time are
  proxies.
- ⏳ Item 3 (adversarial oracle stress): still open and now the dominant residual
  risk. Clean-run false_pass of 0 is necessary but not sufficient; it stays on the
  Phase-1 critical path.

Decision unchanged: proceed to Phase 1, with item 3 as a gating concern before
shared memory is trusted across writers.
