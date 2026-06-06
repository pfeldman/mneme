# 04 — MVP and the decisive experiment

Build order is in `AGENTS.md`. Full protocol + code in
`experiments/ui-mutation/`. This doc states why the MVP is shaped this way.

## The MVP is an experiment, not a product
Phase 0 exists to find out, fast and cheaply, whether the thesis holds. If it
doesn't, no product polish saves it.

## Measurement order is deliberate
**Measurement 1 — the existential gate: memory vs a COLD agent (cost + reliability).**
If a cold, no-memory agent already achieves the goals about as reliably for
similar-or-lower cost, the memory layer has no reason to exist. Run and check
this FIRST. This is the risk most likely to kill the idea (docs/06), so it goes
first, not last.

**Measurement 2 — robustness: memory vs a RECORDED Playwright script (post-mutation).**
Only if M1 clears: inject a UI mutation and check whether knowledge-driven step
regeneration recovers where the recorded script breaks.

**Cross-cutting guardrail — oracle error rate.** Throughout both arms, measure
false-pass (oracle says success while the app is broken) and false-fail. Must
beat brittle-test levels or the shared memory is a liability.

## Scope discipline
- One runtime (Browser Use). One writer. 3–5 flows (login, search, checkout).
- Minimal Phase-0 schema only. No states/paths/risks/uncertainties (Phase 1).
- Seed each goal's success oracle (human/spec) before exploring (ADR-0005).
- Scaffold and fill the metrics harness — it is the most important code here.

## Kill / continue gate (in order)
1. Fails M1 (no cost/reliability edge over a cold agent) → STOP.
2. Passes M1 but fails M2 (no robustness edge over a recorded script) → STOP.
3. Oracle false-pass rate not driveable below brittle-test levels → STOP.
Continue only if it clears all three.
