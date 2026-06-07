# Architecture Decision Records

Short, immutable records of *why* a decision was made. Add a new numbered file
per decision; never edit a superseded one (mark it `Superseded by ADR-XXXX`).

| ADR | Decision |
|-----|----------|
| 0001 | Append-only event log is the source of truth |
| 0002 | The knowledge schema is the neutral interop layer (not a wire protocol) |
| 0003 | Runtime-specific code lives only behind an adapter SPI |
| 0004 | Provenance + confidence are mandatory on every assertion |
| 0005 | Oracle trust by evidence diversity; cold-start via seeded oracles |
| 0006 | Phase-0 status semantics — "uncorroborated" maps to `contested` |
| 0007 | Phase-0 existential gate cleared (provisionally) — proceed to Phase 1 |
| 0008 | Type-diversity needs source-independence (Phase-1 oracle hardening) |
