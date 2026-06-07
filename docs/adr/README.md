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
| 0006 | Phase-0 status semantics - "uncorroborated" maps to `contested` |
| 0007 | Phase-0 existential gate cleared (provisionally) - proceed to Phase 1 |
| 0008 | Type-diversity needs source-independence (Phase-1 oracle hardening) |
| 0009 | Phase 1 scope, regression-recall falsifier, and the praxis reframe |
| 0010 | Phase 1 regression-recall gate cleared (provisionally) - proceed to Phase 1.5 |
| 0011 | Phase 2 scope: five load-bearing items, schema activations, and Phase 1.5 / Phase 3 deferrals (Accepted) |
| 0012 | Multi-writer concurrency contract: file-per-event store, source_id = agent_identity, day-one adversarial harness (Accepted) |
| 0013 | Recency decay as projection-time derivation; status flips emit decay events, anchored by observed_app_version (Accepted) |
| 0014 | E-mode candidate persistence as sibling CandidateEvent type with the same diversity-or-seed promotion rule (Accepted) |
| 0015 | Exploration reward pre-registered, observability-only in Phase 2, paired with adversarial Goodhart review and random-walk baseline (Accepted) |
| 0016 | Real-app SUT selection: pre-registered criteria, Conduit recommended (Saleor fallback), new run dir parallel to Phase 1 (Accepted) |
| 0017 | Additive auth_state projected field (authenticated + scope), adapter-boundary redaction, no tokens/cookies/PII in knowledge (Accepted) |
