# 06 — Risks and failure modes

## The principal risk: the oracle problem
The hardest part of a test is not executing steps — it is knowing what "correct"
is. In this design, fallible agents **author and trust each other's success
criteria.** When a success signal is subtly wrong, you don't get a loud failure;
you get a test that **passes while the app is broken** (or fails while it's
healthy), and that error propagates through shared memory.

The asymmetry is the whole point: **brittleness is loud; bad knowledge is
silent.** A broken selector screams. A confidently-wrong oracle lies quietly.
Shared memory that accumulates confident lies is **worse than no memory.**

Therefore the entire product is really a **trust-management layer** over
agent-authored knowledge. Every design choice should favor making a wrong
assertion loud and traceable over silent and convenient.

### Mitigations (already in the design)
- Append-only log → wrong assertions are traceable and reversible (ADR-0001).
- Provenance + confidence mandatory (ADR-0004).
- Oracle requires ≥2 independent sources before `believed`; flip-floppers
  `quarantined`.
- Contradictions preserved as `contested`, never silently resolved.

## The existential risk: re-derivation gets cheap enough
If frontier models keep getting cheaper/better and self-healing *procedural*
caches (Stagehand-style) keep improving, "let the agent figure it out cold each
run" may dominate "maintain a living semantic model and govern its conflicts."
Then the memory layer is solving a problem capable agents dissolve on their own.
**The Phase 0 experiment must measure this margin directly** (cost vs a cold
agent). If the margin isn't there, the moat isn't there.

## Other failure modes
- **Coverage collapse** — agents converge on the happy path; tests go blind
  (docs/05; counter with exploration incentives).
- **Maintenance cost returns** — if agents can't reliably maintain the model,
  the cost curve that killed classic MBT comes back. Watch human-intervention rate.
- **Shared-state leakage** — secrets/PII bleed across users (docs/05). Redact at
  the boundary; scope per tenant.
- **Schema rot** — over-rich schema nobody fills correctly. Keep it minimal; let
  it grow from real need.
- **Adoption** — a knowledge layer is worthless empty. Ship adapters for runtimes
  people already use; deliver value from a single agent before requiring many.

## What would tell us to stop
- Phase 0 doesn't beat both baselines.
- Oracle error rate is not driveable below brittle-test levels.
- Human-intervention rate to keep memory correct exceeds the cost of the tests
  it replaces.
