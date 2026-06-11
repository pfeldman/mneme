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
| 0018 | Phase 3 scope and the library-plus-git reframe: no SaaS, git is the shared memory, Claude Code is the local brain (Accepted) |
| 0019 | Brain pluggability and execution surfaces: deterministic vs agentic, local Claude Code skill vs CI API-key agent, teach is skill-only (Accepted) |
| 0020 | PyPI packaging and distribution: dist name praxis-qa, one universal wheel, schema and skills as package data, stable public API surface (Accepted) |
| 0021 | The .praxis/ repository convention: git as shared memory, committed knowledge/candidates, gitignored runs and .praxis.secrets, one file per candidate observation (Accepted) |
| 0022 | The teach operation as a Claude Code skill: human-in-the-loop seed, typed prompts, credentials never persisted, no silent overwrite of a believed goal (Accepted) |
| 0023 | praxis regress and explore dual surface: console CLI plus skill, aggregate default, OK/REGRESSED/STALE break-vs-drift report, candidate dedup by trigger (Accepted) |
| 0024 | CI integration by invoking the console commands: Praxis owns no CI machinery, the team owns push/PR/auth, promotion stays a human merge (Accepted) |
| 0025 | Landing page and docs site: minimal non-engineer story, no analytics/signup/SaaS funnel, mkdocs from docs/, documented example CI workflow (Accepted) |
| 0026 | Persistent authenticated-session reuse: reuse the saved browser session so 2FA is not needed every run, session is a secret (local file or CI secret, never knowledge), AUTH-EXPIRED is a third verdict (Proposed) |
| 0027 | Self-contained console test runner driven by a local claude -p brain (subscription, no API key, headless, pytest-style), plus auth-as-subject vs auth-as-precondition: an auth-subject goal performs a real login, a feature goal reuses the session (Accepted) |
| 0028 | Regress agent confirms every believed success signal in its declared type: align the prompt with the exact-type matcher, keep the matcher and Jaccard floor unchanged, never let "confirm all" become "tick all", seed only reproducible types (Accepted) |
| 0029 | Agent self-observations cannot self-certify the oracle: per-summary promotion on its own merit (seeded or genuine different-type different-source corroboration), regress does not persist promotable agent observations, INHERENT seed-rides-single-agent boundary preserved (Accepted) |
| 0030 | Signals as checkable facts with explicit variable slots: a signal value can be a predicate hard on the invariant and tolerant only on declared per-run instance tokens, matched by evaluating the predicate (no Jaccard), additive over the free-text path, never activating deferred states/paths (Accepted) |
| 0031 | Signals as structured checks for relational and after-action facts: an optional typed `check` (list_count_delta, element_membership) evaluated programmatically over self-reported before/after observation data, the stricter third tier above value_predicate, agent self-reports the baseline (no runner change), never a false PASS, never activating deferred states/paths (Accepted) |
| 0032 | Whether an observed-but-still-streaming network request counts as a typed partial confirmation: teach-time guidance ships now (do not type a streaming/long-lived endpoint `network`, type the visible effect behaviorally/text), the runtime PARTIAL/STREAMING verdict is left to the maintainer because it touches the ADR-0023 taxonomy and risks excusing a hung-stream regression (Proposed) |
