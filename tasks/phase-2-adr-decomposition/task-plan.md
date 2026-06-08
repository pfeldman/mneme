---
type: task-plan
ticket: null
mode: freetext
created: 2026-06-07
status: approved
approval_notes: |
  Pablo approved 2026-06-07. All 5 open decisions resolved per recommendation:
  1. ADR-0010 already Accepted, new ADRs reference it unconditionally.
  2. README format: append ` (Proposed)` inline; no new column.
  3. Invariants: each ADR cites the ones that apply; not exhaustive across batch.
  4. ADR-0013 owns the multi-writer/decay collision; ADR-0012 stays silent.
  5. Commits land WITHOUT Co-Authored-By trailer.
---

# Plan: Phase 2 ADR decomposition (0011-0017)

## Pre-flight observations

- Branch is `claude/mneme-phase-1` as required; working tree is clean. No new branch needed.
- `docs/adr/README.md` is a markdown table with columns `| ADR | Decision |`. Each row is `| NNNN | one-line summary |`. No explicit "Status" column. The brief's acceptance criterion mentions "status: Proposed" - cleanest match is to append ` (Proposed)` inline to each new row's summary rather than introducing a third column (which would touch every prior row). Flagged as Open decision.
- Existing ADR files run 0001 through 0010. Next available is 0011, matching the brief.
- **IMPORTANT DISCREPANCY**: The brief states ADR-0010 (Phase 1 verdict) "is NOT yet written and will be authored by a separate task after the live run finishes." But `docs/adr/0010-phase-1-regression-recall-verdict.md` already EXISTS with `Status: Accepted (2026-06-07)` and its row is already in `docs/adr/README.md`. The brief's instructions to "not pre-empt ADR-0010" and "ADR-0010 row is NOT added by this task" are moot. The seven new ADRs can reference ADR-0010 as Accepted unconditionally rather than hedging. Flagged for Pablo before the loop starts.
- `KnowledgeFile` / `KnowledgeAdapter` naming and `praxis` rename are already in ADR-0009 (Accepted). New ADRs can use `praxis` paths freely.
- The brief's invariant list contains 21 names. Two of them (`single-runtime-coupling-forbidden` and `runtime-agnostic-core`) look like restatements of the same property from AGENTS.md non-negotiable 4. Each ADR cites whichever apply; not every ADR cites both.

## Steps

- [x] Step 1: Author ADR-0011 (Phase 2 scope umbrella, defers auditor/Stagehand to Phase 1.5 and governance/RBAC/etc to Phase 3, lists five load-bearing Phase 2 items, resolves Phase-1.5-vs-Phase-2 boundary item by item; covers P2-02, P2-20, P2-21, P2-22, P2-23, P2-24).
  - Files: `docs/adr/0011-phase-2-scope-and-deferrals.md`
  - Verification: file exists; `Status: Proposed`; Context cites P2-02 / P2-20..24 by id; Decision lists the five load-bearing items + names every deferred item with target phase; Consequences names invariants respected (operational-knowledge-not-procedures, knowledge-not-mbt-procedure-cache, schema-is-single-source-of-truth, append-only-store-no-mutation) and invariants explicitly NOT covered by this ADR (concurrent-writes-lose-no-knowledge - punted to 0012, exploration-incentive-against-coverage-collapse - punted to 0015, tenant-scoping-prevents-leakage - punted to 0012); explicit "Relation to prior ADRs" section (Extends ADR-0009, builds on ADR-0010 verdict); no implementation code; `bash verify.sh` ALL GREEN.

- [x] Step 2: Author ADR-0012 (multi-writer concurrency contract with two named sections: store-layer + gate-layer; single-tenant-by-contract placeholder with `store/<tenant_id>/events/` path convention; named subsection committing the adversarial harness to ship in the same commit as multi-writer store changes; CI gate refuses merge without scenarios in `experiments/multi_writer/`; covers P2-03, P2-05, P2-19).
  - Files: `docs/adr/0012-multi-writer-concurrency-contract.md`
  - Verification: file exists; `Status: Proposed`; Context cites P2-03, P2-05, P2-19; Decision has two NAMED sections (a) store-layer (b) gate-layer plus a NAMED subsection "Adversarial harness ships in the same commit"; explicit forbidden alternatives (no last-write-wins, no in-place mutation, no non-deterministic shard merging, no consensus synthetic entries, no `source_id = run_uuid`); Consequences names invariants respected (append-only-store-no-mutation, concurrent-writes-lose-no-knowledge, no-self-corroboration-source-independence, contradictions-preserved-as-contested, tenant-scoping-prevents-leakage, loud-and-traceable-over-silent-and-convenient) and invariants NOT covered (exploration-incentive-against-coverage-collapse, no-secrets-tokens-pii-in-knowledge); Relation to prior ADRs (Extends ADR-0001, Refines ADR-0008, Extends ADR-0005); no implementation code; verify.sh ALL GREEN.

- [x] Step 3: Author ADR-0013 (recency decay as projection-derived demotion: confidence shifts are pure derivation, status flips emit explicit decay events; diversity re-evaluation at projection time; same-type repeats cannot keep `believed` alive; `observed_app_version` is primary anchor, wall-clock secondary; full ownership of multi-writer-and-decay collision; covers P2-04).
  - Files: `docs/adr/0013-recency-decay-as-projection-derivation.md`
  - Verification: file exists; `Status: Proposed`; Context cites P2-04; Decision specifies projection-time re-evaluation of `independent_diverse(...)`, explicit decay-event append for status flips, unidirectional decay; forbidden alternatives (no in-place confidence mutation, no silent status drift, no same-type repeat keeping `believed` alive); names how the multi-writer/decay `observed_app_version` collision resolves (does NOT punt to 0012); Consequences names invariants respected (append-only-store-no-mutation, oracle-diversity-rule, no-self-corroboration-source-independence, loud-and-traceable-over-silent-and-convenient, contradictions-preserved-as-contested) and invariants NOT covered (tenant-scoping-prevents-leakage); Relation to prior ADRs (Extends ADR-0001, Refines ADR-0005/0008, depends on ADR-0012 for source_id semantics); no implementation code; verify.sh ALL GREEN.

- [x] Step 4: Author ADR-0014 (E-mode candidate persistence as sibling `CandidateEvent` type with its own `schema_version`; default status `contested`; promotion via `independent_diverse(...)` from `oracle/trust.py` unchanged; same-`agent_identity` writers remain one source; `praxis review` surfaces a queue; human promotion writes a NEW seed event; unresolved candidates decay to `stale` per ADR-0013; covers P2-17, P2-18).
  - Files: `docs/adr/0014-e-mode-candidate-persistence.md`
  - Verification: file exists; `Status: Proposed`; Context cites P2-17, P2-18 and the Phase 1 deferral recorded in ADR-0009; Decision specifies CandidateEvent is a sibling NOT an extension of `ObservationEvent`, candidates default `contested`, same promotion rule, multi-writer inheritance from ADR-0012 + ADR-0008 INHERENT boundary; forbidden alternatives (no relaxed promotion, no edit-in-place of candidate event on human promotion, no silent removal of unresolved candidates); `risks.trigger` validator from ADR-0009 applies on write; Consequences names invariants respected (provenance-and-confidence-mandatory, append-only-store-no-mutation, no-self-corroboration-source-independence, oracle-diversity-rule, schema-is-single-source-of-truth, loud-and-traceable-over-silent-and-convenient, contradictions-preserved-as-contested) and invariants NOT covered (exploration-incentive-against-coverage-collapse - 0015, tenant-scoping-prevents-leakage - 0012); Relation (Extends ADR-0001/0009, depends on ADR-0012/0013, Refines ADR-0008); no implementation code; verify.sh ALL GREEN.

- [x] Step 5: Author ADR-0015 (exploration reward pre-registration: `reward = (resolved_uncertainties + alpha * new_unique_candidate_risks) / budget_tokens` with canonicalization via trigger validator; observability-only for Phase 2; Goodhart adversarial review required before any run; random-walk baseline runs concurrently; new floor `unique_candidates_per_budget` and red-flag `goodhart_score`; alpha + resolution criteria sealed under `praxis_git_sha` at run-start; covers P2-06, P2-07).
  - Files: `docs/adr/0015-exploration-reward-pre-registration.md`
  - Verification: file exists; `Status: Proposed`; Context cites P2-06, P2-07; Decision states exact formula, `unique` canonicalization via ADR-0009 trigger validator, alpha pre-registered, observability-only, adversarial review deliverable shape, baseline concurrency requirement; forbidden alternatives (no feeding reward back to agent state in Phase 2, no alpha change post-run without invalidating data, no metric reporting without paired adversarial review); Consequences section contains the explicit clause "reporting a metric IS optimizing for it"; names invariants respected (exploration-incentive-against-coverage-collapse, loud-and-traceable-over-silent-and-convenient, no-silent-success-when-app-broken, provenance-and-confidence-mandatory) and invariants NOT covered (tenant-scoping-prevents-leakage); Relation (Extends ADR-0009, depends on ADR-0014); no implementation code; verify.sh ALL GREEN.

- [x] Step 6: Author ADR-0016 (real-app SUT selection: pre-registered criteria including 30-min dockerizability and 4-5 goals comparable to Phase 1; evaluation of Conduit / Saleor / OpenMRS; recommendation Conduit with Saleor fallback; pre-registers `experiments/regression_recall_real/` as new directory leaving Phase 1 sealed artifacts untouched; covers P2-08, P2-09, P2-10 SUT half).
  - Files: `docs/adr/0016-real-app-sut-selection.md`
  - Verification: file exists; `Status: Proposed`; Context cites P2-08, P2-09, P2-10; Decision lists pre-registered criteria, evaluates three candidates per criterion, recommends Conduit (fallback Saleor), names 4-5 Phase 2 goals parallel to Phase 1, names `experiments/regression_recall_real/` as the new directory; forbidden alternatives (no modification of Phase 1 sealed artifacts, no piggybacking of new schema fields here - punted to 0017); Consequences names invariants respected (knowledge-not-mbt-procedure-cache, operational-knowledge-not-procedures, no-procedures-secrets-or-run-data-as-storage-targets, runtime-agnostic-core) and invariants NOT covered (no-secrets-tokens-pii-in-knowledge - 0017, schema-is-single-source-of-truth - 0017); Relation (Extends ADR-0009); no implementation code; verify.sh ALL GREEN.

- [x] Step 7: Author ADR-0017 (additive schema extension `auth_state: {authenticated: bool, scope: string|null}`; redaction stays at adapter boundary; `schema/knowledge.schema.json` + pydantic model + model-schema agreement test must update together in a FUTURE commit; ADR is separate from 0016 because bundling SUT-pick with PII-fragile schema is the canonical leak shape; covers schema half of P2-10).
  - Files: `docs/adr/0017-schema-extension-auth-state.md`
  - Verification: file exists; `Status: Proposed`; Context cites schema half of P2-10 and references ADR-0016 for the SUT pressure that surfaced this need; Decision names exact projected shape, enumerates what `auth_state` MUST NOT carry (tokens, cookies, user IDs, session IDs, anything matching no-secrets-tokens-pii-in-knowledge), states adapter-boundary redaction stays the rule, names that schema + pydantic + agreement test update in same future commit; explicit "this ADR is separate from 0016 because..." clause; forbidden alternatives (no tokens, no IDs, no schema bundling with SUT-pick); Consequences names invariants respected (schema-is-single-source-of-truth, no-secrets-tokens-pii-in-knowledge, invariants-not-coordinates-hierarchy, adapter-spi-tiny-and-stable, loud-and-traceable-over-silent-and-convenient) and invariants NOT covered (concurrency-related ones - 0012); Relation (Extends ADR-0002 schema-as-interop, depends on ADR-0016, Refines ADR-0003 adapter boundary); no implementation code; verify.sh ALL GREEN.

- [x] Step 8: Update `docs/adr/README.md` to add seven rows (0011 through 0017) following the existing two-column table format. Per-row summary: one line capturing the decision; append ` (Proposed)` to each new row's summary since the table has no status column. Existing rows for ADR-0001 through ADR-0010 unchanged.
  - Files: `docs/adr/README.md`
  - Verification: `git diff docs/adr/README.md` shows only seven new rows appended after the ADR-0010 row, no other changes; each new row matches existing column shape; `bash verify.sh` ALL GREEN.

- [x] Step 9: Final verification (no commit unless a fix is needed). Run `bash verify.sh` ALL GREEN; run `git diff --name-only $(git merge-base HEAD claude/mneme-phase-0-core-ZfmKT)...HEAD` and cross-check against the sealed-artifact list to confirm NO sealed path appears; confirm seven new ADR files exist via `ls docs/adr/001[1-7]-*.md`; confirm `docs/adr/README.md` contains seven new rows (`grep -c '^| 001[1-7]' docs/adr/README.md` returns 7).
  - Files: none (verification only)
  - Verification: verify.sh ALL GREEN; sealed-artifact diff is empty; seven new ADR files present; README has seven new rows. If any check fails, fix the offending step's ADR in a follow-up commit (NEVER amend prior commits in the loop).

## Pre-conditions

- Branch: `claude/mneme-phase-1` (already current; do NOT create a new branch).
- Working tree: currently clean.
- Lint to keep green: `bash verify.sh` (must end ALL GREEN after every step; for doc-only commits this should be trivial since no pytest / ruff / mypy / harness targets touch `docs/adr/`).
- Sealed artifacts the loop MUST NOT touch (from the brief Constraints section):
  - `experiments/regression_recall/pre_registration.md`, `LOCAL_RUN.md`, `manifest.json`, `manifest.py`, `README_FROZEN.md`, `cold_readme_per_goal.md`, `judge_prompt.txt`, `metrics.py`, `harness.py`, `run_live.py`, `exec_anthropic.py`, `budget.json`
  - `experiments/regression_recall/knowledge/{login,search,checkout,admin_access}.knowledge.yaml`
  - `experiments/regression_recall/runs/phase-1-r1-1780847475/` (entire directory)
  - `experiments/ui-mutation/testapp.py`, `mutate.py`, `runtimes.py`
  - `src/praxis/runner/`, `src/praxis/model/`, `src/praxis/store/`, `src/praxis/oracle/`, `src/praxis/merge/`, `src/praxis/adapters/`, `src/praxis/cli/`
  - `schema/`
  - `pyproject.toml`
  - Also NOT modified by this task: `AGENTS.md`, `docs/01-vision-and-thesis.md`, `docs/06-risks-and-failure-modes.md`, `docs/phase-2-plan.md`.

## Risks / unknowns

- **ADR-0010 status mismatch with brief.** Brief was written assuming ADR-0010 not yet present; file is present and Accepted. Several ADRs (especially 0011, 0016) were planned to hedge conditionally on Phase 1 verdict. Recommendation: drop the hedging. Flagged for Pablo confirmation.
- README table has only two columns (`ADR | Decision`). Adding a Status column mutates all prior rows. Plan appends ` (Proposed)` inline.
- 21-invariant list has near-overlaps (`runtime-agnostic-core` vs `single-runtime-coupling-forbidden`; `tenant-scoping-prevents-leakage` vs `no-secrets-tokens-pii-in-knowledge`). Plan trusts the implementation agent to pick names that genuinely apply per ADR.
- ADR-0012's single-tenant-by-contract placeholder + path convention documents what Phase 2 implementation will enforce; cannot be enforced now because file_store is a sealed artifact in this task.
- ADR-0015 Goodhart adversarial review creates dependency on a future deliverable. This ADR documents the gate; the artifact is Phase 2 implementation work.
- ADR-0013 says it owns the multi-writer/decay collision; ADR-0012 stays silent. Confirm that asymmetry is acceptable (brief mandates strict numeric order, can't swap).

## Open decisions (Pablo to resolve before approving)

1. **ADR-0010 already Accepted**: confirm the new ADRs unconditionally reference it (recommended) instead of hedging.
2. **README format**: append ` (Proposed)` inline to each new row (recommended) vs add a third Status column (mutates all prior rows).
3. **Invariant citation rule**: every ADR cites the invariants from the 21-name list that apply to it (recommended) vs every invariant in the list appears in at least one ADR in this batch.
4. **0012/0013 boundary**: confirm ADR-0012 stays silent on the multi-writer/decay collision and ADR-0013 owns it.
5. **Co-Authored-By trailer**: confirm commits land WITHOUT a Claude attribution trailer (default for this repo; existing commits on the branch follow that pattern).
