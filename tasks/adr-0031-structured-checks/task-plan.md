---
type: task-plan
ticket: null
mode: freetext
adr: 0031
created: 2026-06-09
status: pending-approval
---

# Plan: ADR-0031 signals as structured checks (build)

Goal: implement the typed `check` on a signal (`list_count_delta` +
`element_membership`), evaluated programmatically over a self-reported
before/after observation, then prove it live by turning `delete-a-campaign`'s
false REGRESSED into a real PASS while a planted regression still fails loudly.

Conventions (this repo, from AGENTS.md): branch off `main`, one commit = one
logical change, plain one-line commit messages, no Co-Authored-By, `bash
verify.sh` ends ALL GREEN before every commit, gh PR merged on Pablo's OK.

## Steps

- [ ] Step 1: Add the pure check module `src/praxis/model/check.py`
  - Pydantic models `ListCountDeltaCheck` (`kind: list_count_delta`,
    `expect_delta: int`) and `ElementMembershipCheck` (`kind:
    element_membership`, `identifier_slot: str` min_length 1 + valid slot-name,
    `expect: Literal["present","absent"]`); `Check = Annotated[union,
    discriminator="kind"]`, mirroring `Trigger` in knowledge.py.
  - Pure `evaluate_check(check, observed: dict | None) -> bool`, FAIL-CLOSED:
    `observed is None` / missing keys / wrong types / empty identifier ->
    False. `list_count_delta`: needs int `before_count` + `after_count`,
    returns `after-before == expect_delta`. `element_membership`: needs
    non-empty `identifier` + bool `present`, returns `present == (expect ==
    "present")`.
  - Zero runtime/browser deps (ADR-0003), same posture as `predicate.py`.
  - Files: `src/praxis/model/check.py` (new), `src/praxis/model/__init__.py`
    (export the check types).
  - Verification: new unit tests in step 7; module imports clean, mypy/ruff ok.

- [ ] Step 2: Add `check` to the `Signal` model + JSON schema (keep agreement green)
  - `Signal.check: Check | None = None` in `knowledge.py`, with docstring
    naming it the third tier above `value_predicate` (ADR-0031). Pydantic
    discriminated union gives unknown-kind + field-type rejection for free at
    the write boundary (decision 6); no custom validator needed beyond the
    field constraints in step 1.
  - Add `$defs` (`list_count_delta_check`, `element_membership_check`, `check`
    union) + `"check": {"$ref": "#/$defs/check"}` on the Signal definition in
    `schema/knowledge.schema.json`, mirroring how `trigger` is wired.
  - Files: `src/praxis/model/knowledge.py`, `schema/knowledge.schema.json`.
  - Verification: `tests/test_model_schema_agree.py` and
    `tests/test_schema_examples_validate.py` stay green.

- [ ] Step 3: Carry the structured observation on `ObservedSignal`
  - Add optional `observed: dict[str, Any] | None = None` to `ObservedSignal`
    (`store/events.py`) to carry the per-run payload (before/after counts, or
    identifier+present). `extra="forbid"` already declared; the new field is
    additive and defaults None so every existing payload is unaffected.
  - Files: `src/praxis/store/events.py`.
  - Verification: existing store/projection tests stay green.

- [ ] Step 4: Matcher dispatch `check -> value_predicate -> Jaccard`
  - In `regression._value_matches`, add a `check` branch BEFORE the
    `value_predicate` branch: exact-type equality still gates first (ADR-0028,
    unchanged); if `target.check is not None` -> `return
    evaluate_check(target.check, observed.observed)`; else predicate; else
    Jaccard. Local import of `evaluate_check` to keep the cycle one-way.
  - Files: `src/praxis/runner/regression.py`.
  - Verification: matcher tests in step 7, including the no-false-PASS gate.

- [ ] Step 5: Thread `check` through the projection read path
  - Generalize `_restore_predicates` (projection.py) to also restore `check`
    onto the projected believed signal keyed by `(type, value)` (rename to
    `_restore_seed_signal_fields`, copy both `value_predicate` and `check`);
    update the two call sites in `_carry_seed_only_fields`.
  - Files: `src/praxis/merge/projection.py`.
  - Verification: a NEW read-path test (project_with_seed -> a seeded `check`
    survives onto the believed signal) in step 7. This is the hard-won lesson:
    a field dropped by the projection is silently dead end-to-end.

- [ ] Step 6: Surface the check in the regress prompt + carry `observed` from the executor
  - `prompts._format_signal`: when `sig.check` is present, render a line
    telling the agent which structured fields to report (before/after counts,
    or the identifier to track + whether it is present after the action).
    Extend the regress emit contract so a check signal reports its `observed`
    payload, not free prose.
  - Confirm `_parse_executor_result` carries `observed` (it round-trips through
    `ObservedSignal.model_validate`, so the field rides automatically once
    declared in step 3; add an assertion test).
  - Files: `src/praxis/runner/prompts.py` (+ a targeted prompt test).
  - Verification: prompt test asserts the check line renders; executor-parse
    test asserts `observed` survives.

- [ ] Step 7: Tests + verify.sh ALL GREEN
  - Unit: `tests/test_check.py` (evaluate for both kinds, fail-closed on
    None/missing/empty/wrong-type, validation rejects bad kind / non-int delta
    / empty slot / bad expect).
  - Matcher: check branch matches a good observation; NO-FALSE-PASS gate (a
    planted regression: after==before for a -1 delta, or identifier still
    present for expect=absent -> NOT matched -> UNCERTAIN/REGRESSED, never
    PASS).
  - Read-path: seeded `check` survives `project_with_seed`.
  - Run `bash verify.sh` -> ALL GREEN. (Commit boundary: tests land with the
    code they cover across steps 1-6; this step is the final sweep + any
    gaps.)

- [ ] Step 8: Live proof on the dogfood app (re-seed + run) [VERIFICATION, may span the gate]
  - Re-seed `delete-a-campaign` (praxis-digioh) splitting the compound network
    prose signal into a `list_count_delta` (-1) signal and an
    `element_membership` (absent of the archived id) signal; keep the text
    confirmation as its ADR-0030 predicate.
  - `rm -rf .praxis/runs`, run `praxis regress --goal delete-a-campaign` live
    (headless, claude -p, ~2-4 min) -> expect a real PASS (was false
    REGRESSED).
  - Plant a regression (a check that cannot hold) and confirm it still fails
    loudly.
  - This is the live evidence Pablo needs before marking ADR-0031 Accepted; it
    is NOT a code commit on the branch (the seed lives in praxis-digioh).

## Pre-conditions
- Branch to create: `adr-0031-build` off `main`.
- verify.sh ALL GREEN before each commit; PR opened on completion, merged on
  Pablo's OK.

## Risks / unknowns
- The model<->schema agreement test is strict; the `check` `$defs` must match
  the pydantic union exactly (field names, discriminator, required keys). Step
  2 is where drift would surface.
- `observed: dict[str, Any]` is intentionally loose (not a typed payload per
  kind) so the brain can emit either shape; the fail-closed evaluator is the
  guard. A typed payload is a possible future tightening, not this build.
- Live run (step 8) depends on the praxis-digioh stack + subscription claude
  -p; it is the only non-deterministic step.

## Open decisions
- None blocking. The design forks (vocabulary = 2 kinds; baseline = agent
  self-report) were resolved with Pablo before the ADR and are fixed by
  ADR-0031. Steps 1-7 are deterministic code; step 8 is the live verification
  that gates marking the ADR Accepted (Pablo's call, not mine).
