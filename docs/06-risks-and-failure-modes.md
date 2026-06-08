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

## Oracle self-pollution: regress self-certifying the oracle (ADR-0029)

A specific instance of the principal risk, found in a live regress run and now
closed. Regress is meant to READ the believed oracle (the seeded success/failure
signals) and report a verdict; it must never GROW the believed set. Two
compounding defects let it grow the set and self-certify:

- Defect A (the runner persisted agent observations). The regress runner appended
  every agent confirmation as a promotable observation event on each run, so a
  read became a write of promotable evidence. The believed success set grew run
  after run.
- Defect B (a lone agent summary borrowed the goal-level flag). The per-signal
  classifier promoted an agent success summary to believed when the GOAL had a
  seeded independent-diverse oracle, even if that exact summary's only source was
  a single agent of a type the seeds already covered. The summary rode the
  goal-level independence the seeds supplied, without any different-type,
  different-source signal corroborating it.

Together they inflated the create-welcome-popup goal from 4 seeded believed
success signals to 26 agent-sourced ones, which froze the goal at UNCERTAIN (no
single run reproduces 26 paraphrases, so the all-believed-observed pass condition
could never be met). This is the principal risk in its purest form: shared memory
accumulating confident, agent-authored claims that were never independently
corroborated.

Mitigations (ADR-0029, now in the design):

- Regress does not persist agent observations as promotable evidence by default.
  The verdict is computed in-memory from what the run observed; the believed set
  grows only from a human/spec seed (teach) or genuinely independent-diverse
  evidence, never from a confirmation run of one agent.
- Promotion is decided PER SUMMARY on the summary's own merit: a success summary
  is believed only when it is itself seeded OR itself participates in genuine
  corroboration (a different-type partner from a different source). The
  goal-level independence flag can no longer be borrowed by an unrelated
  lone-agent summary. The ADR-0008 INHERENT boundary (a seed plus one genuine
  different-type agent observation) is preserved; only the unbounded same-type
  self-restatement is refused.

### Per-machine recovery from a polluted run log

If a run log under `.praxis/runs/` was already polluted by the old behavior (it
holds agent observation events that grew a goal's believed set), the recovery is
a per-machine reset, not a code change and not a destructive script Praxis ships:

- The run log under `.praxis/runs/` is gitignored and per-machine (ADR-0021): it
  is a local cache, not shared knowledge. The committed seed under
  `.praxis/knowledge/` is the source of truth for the oracle.
- To recover, a maintainer deletes the local gitignored `.praxis/runs/` directory
  by hand on the affected machine and lets the next run re-derive the believed
  state from the committed seed. Nothing in the committed history is touched; the
  append-only store stays intact (ADR-0001).
- Praxis deliberately ships NO destructive reset command. Deleting a local cache
  is a one-line manual action a human takes knowingly; a built-in "wipe the runs"
  command would be a foot-gun that could erase a machine's only copy of an E-mode
  candidate stream that was not yet committed. The reset stays manual and local.

## What would tell us to stop
- Phase 0 doesn't beat both baselines.
- Oracle error rate is not driveable below brittle-test levels.
- Human-intervention rate to keep memory correct exceeds the cost of the tests
  it replaces.
