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
