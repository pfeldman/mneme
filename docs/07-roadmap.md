> **Reference / Phase 1+.** Orientation for after the Phase-0 gate passes; not required to run the experiment.

# 07 — Roadmap and form factor

## Form factor: library → product → standard (in that order)
- **Open-source library + schema (adoption).** The only way to seed adoption and
  give the format a chance to become de-facto. Position as a pluggable memory
  backend *for runtimes people already use*, not a competitor to them.
- **Hosted trust layer (the product / the moat).** Provenance, conflict
  resolution, poisoning detection, secret redaction, governance, dashboards,
  retention/decay policy, hosted shared memory. Nobody pays for a YAML schema;
  they pay for someone to guarantee the shared memory doesn't rot or lie. The
  hard parts in docs/05–06 *are* the product.
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

### Phase 3 — Trust / product layer
Governance, secret redaction, provenance graph + dashboards, poisoning
detection, hosted shared memory, per-tenant scoping, retention policies. Begin
monetization here.

### Phase 4 — Interop / de-facto standard
Stabilize the schema and adapter SPI; grow community adapters across runtimes;
publish the spec properly once it has traction.

## Leading indicators to watch
- Phase 0 cost margin vs a cold agent (existential-risk gauge, docs/06).
- Recovery rate on UI mutation vs recorded scripts.
- Oracle false-pass rate (must beat brittle tests).
- Human-intervention rate to keep memory correct (the MBT-cost gauge).
- For Phase 1+: adapters in use, knowledge entries that stay `believed` across releases.
