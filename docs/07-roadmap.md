> **Reference / Phase 1+.** Orientation for after the Phase-0 gate passes; not required to run the experiment.

# 07 — Roadmap and form factor

## Form factor: library → product → standard (in that order)
- **Open-source library + schema (adoption).** The only way to seed adoption and
  give the format a chance to become de-facto. Position as a pluggable memory
  backend *for runtimes people already use*, not a competitor to them.
- **The trust layer, shipped as library plus git (the product).** Provenance,
  conflict resolution, decay, secret redaction, and the believed-vs-contested
  oracle are the hard parts (docs/05-06) and they ARE the product, but Phase 3
  ships them in the library and the git convention, not as a hosted service
  (ADR-0018). Knowledge is shared through git; there is no hosted backend and
  no monetization layer.
- **Standard (a consequence, never the opening move).** Standards capture little
  value directly; value accrues to whoever runs the trusted memory service. If
  the format wins, it standardizes itself.

## Phases
### Phase 0 — Validate (weeks)
The UI-mutation experiment. One runtime, one writer, three flows, three metrics,
a kill/continue gate. Output: a yes/no on the thesis with numbers. (docs/04)

### Phase 1 — OSS memory library (months)
Harden model/store/merge. Second adapter (Stagehand) + a benchmark against its
action cache. Publish schema + docs + examples. Goal: a single agent gets real
value (cheaper, more robust runs) with zero multi-agent complexity.

### Phase 2 — Multi-agent
Concurrent writers, contradiction detection, recency decay, quarantine, and an
explicit exploration incentive against coverage collapse. MCP memory-server
surface for cross-agent sharing.

### Phase 3 - Library plus git, no SaaS
Ship Praxis as a pip-installable library (`pip install praxis-qa`) plus a git
convention, not a hosted service (ADR-0018). A repo's knowledge lives under
`.praxis/` and is shared through git (`git pull` / `git push`), with one repo
per project, git permissions, and git log as the audit trail. The reasoning
brain is pluggable: local is Claude Code via skills (no API key), CI is an
API-key agent the team wires into its own CI (ADR-0019, ADR-0024). The
authoring loop is the `praxis teach` skill, regression is `praxis regress`
with an OK / REGRESSED / STALE report, and exploration is `praxis explore`.
The hosted trust-layer items the earlier roadmap listed (governance,
dashboards, hosted shared memory, monetization) are replaced by git-native
equivalents per ADR-0018 through ADR-0025; there is no SaaS.

### Phase 4 — Interop / de-facto standard
Stabilize the schema and adapter SPI; grow community adapters across runtimes;
publish the spec properly once it has traction.

## Leading indicators to watch
- Phase 0 cost margin vs a cold agent (existential-risk gauge, docs/06).
- Recovery rate on UI mutation vs recorded scripts.
- Oracle false-pass rate (must beat brittle tests).
- Human-intervention rate to keep memory correct (the MBT-cost gauge).
- For Phase 1+: adapters in use, knowledge entries that stay `believed` across releases.
