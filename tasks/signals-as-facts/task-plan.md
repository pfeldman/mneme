---
type: task-plan
ticket: null
mode: freetext
created: 2026-06-08
status: pending-approval
---

# Task plan: signals as checkable facts (ADR-0030)

Implementation plan for ADR-0030. This is DESIGN-APPROVED-PENDING: it changes the
DATA MODEL (a new optional field on `Signal`) and the MATCHER (the load-bearing
guard against a false PASS), so it needs Pablo's explicit approval of the approach
and of the Open Decisions below BEFORE any code is written.

This is the most fundamental change in the current batch (it touches the matcher
and the verdict area, the oracle-sacred core). It should SEQUENCE AFTER the
oracle-self-certification fix (ADR-0029, branch `oracle-no-self-certification`)
merges, because both edit the matcher / verdict region of `regression.py` and
landing them in series avoids a hard conflict and keeps each change reviewable in
isolation. Rebase this work onto the post-0029 integration tip before starting.

## Pre-conditions

- ADR-0030 is approved (Status moves Proposed -> Accepted) and the Open Decisions
  below are resolved by Pablo.
- ADR-0028 (prompt-matcher type alignment) is merged: this plan keeps the
  exact-type equality ADR-0028 relies on and the prompt contract it shipped.
- ADR-0029 (oracle self-certification fix) is merged; rebase onto its tip so the
  two matcher-area changes do not collide.
- `bash verify.sh` is ALL GREEN on the base before starting (baseline).
- The model<->schema agreement test (`tests/test_model_schema_agree.py`) is green
  on the base; it will gate every schema/model step.

## Steps (commit-sized, one checkbox per commit)

- [ ] 1. Schema: add the optional structured-predicate field to the `signal`
      `$defs` in `schema/knowledge.schema.json`. Per Open Decision A, this is
      either a new optional `value_predicate` string property OR an optional
      `slots` companion list; do NOT change the required list (`type`, `value`,
      `provenance`, `confidence`, `status` stay required) so every existing seed
      validates unchanged. `additionalProperties: false` stays. Add the field
      description naming the variable-slot `{name}` / `{name:shape}` convention.
      DoD: `schema/examples/login.knowledge.yaml` and the experiment seeds still
      validate.

- [ ] 2. Model: mirror the schema field on the pydantic `Signal` in
      `src/praxis/model/knowledge.py` as an optional field (default None), so a
      free-text signal is unchanged. Keep `extra="forbid"`. Run the
      model<->schema agreement test; fix drift until green. No matcher change yet.
      DoD: agreement test green, existing model tests green.

- [ ] 3. Predicate parser + validator: add a small pure module (e.g.
      `src/praxis/model/predicate.py`) that (a) parses a predicate template into
      (invariant text, ordered list of `(slot_name, shape)`), (b) validates it
      per ADR-0030 decision 6 (reject no-invariant, malformed slot, unknown
      shape, stopword-only invariant reusing `regression._STOPWORDS` or a shared
      copy), (c) given an OBSERVED value, evaluates holds/does-not-hold per
      decision 2 (exact invariant match case-folded + whitespace-normalized;
      every declared slot filled by a non-empty token; optional shape check per
      decision 5). Pure, zero runtime deps (AGENTS.md non-negotiable 4). Unit
      tests cover: exact-invariant pass, wrong-status-code fail, slot-filled
      pass, empty-slot fail, numeric-shape pass/fail, no-invariant reject,
      malformed-slot reject, stopword-only reject.

- [ ] 4. Wire the validator at the write boundary: a seed (or observation) that
      declares a structured predicate is validated where seeds enter, the same
      posture as `trigger_validator.validate_trigger`. A malformed predicate is a
      loud rejection (pydantic validator on `Signal`, or adapter-boundary check),
      never a silent downgrade to free-text. DoD: a malformed-predicate seed
      fails validation with a clear message; a valid one passes.

- [ ] 5. Matcher: add the structured branch to `_value_matches` in
      `src/praxis/runner/regression.py` BEFORE the Jaccard arm. Keep exact-type
      equality first (unchanged). If the target signal carries a structured
      predicate, evaluate it via the decision-3 parser against the observation's
      value and return its holds/does-not-hold result; Jaccard is NOT computed.
      If the target has no predicate, fall through to the unchanged Jaccard path
      (decision 4). `verdict_from_observations` is UNCHANGED.
      DoD: a unit test reproduces the live `create-welcome-popup` case (abstract
      seed slot, concrete observed instance) and asserts the three formerly-missed
      signals (url/text/network) now match, while a wrong-invariant observation
      (non-2xx, wrong route) still does NOT match.

- [ ] 6. Regression-suite proof of no-false-pass: add tests asserting a planted
      regression (failure-invariant observed, or a believed success invariant
      genuinely absent) still produces FAIL / UNCERTAIN, NOT PASS, under the
      structured path. This is the load-bearing guard; it must be explicit.

- [ ] 7. Re-seed ONE dogfood goal into the structured form as the worked example
      (per Open Decision C, recommend `create-welcome-popup` or a single Conduit
      goal). Leave all other seeds free-text (they keep matching the old way).
      DoD: the re-seeded goal validates and a live/offline run matches all its
      believed success signals.

- [ ] 8. Docs: update `docs/03-knowledge-schema.md` (and the relevant
      `docs/examples/*.md` if it shows a signal) to document the structured
      predicate + slot convention. Update `schema/examples/login.knowledge.yaml`
      with a COMMENTED structured-signal example (kept free-text-compatible) so a
      seed author sees the shape. DoD: examples validate; docs name the
      invariant-vs-slot distinction.

- [ ] 9. `bash verify.sh` ALL GREEN; `ruff` + `mypy` clean. Open the PR (draft
      unless Pablo has validated a live run; ready-for-review only after a green
      live regress run on the re-seeded goal).

## Risks / unknowns

- The matcher is the false-PASS guard. The dominant risk is a structured-path bug
  that admits a wrong invariant as a match. Mitigation: step 6 is a dedicated
  no-false-pass test gate; the invariant is matched EXACTLY (stricter than
  Jaccard), so the structured path should be strictly harder, not looser.
- The "invariant vs slot" boundary is an authoring judgment. A token left in the
  invariant that actually varies per run will produce a NEW false UNCERTAIN; a
  token pulled into a slot that should be fixed produces a too-loose match. The
  validator catches the structural worst cases but not a semantically mis-drawn
  boundary; teach-time guidance (out of scope here, ADR-0022) is the longer-term
  mitigation.
- Whitespace / case normalization of the invariant must match the brain's actual
  output shape. If the agent reports `Returns 2xx` and the seed says `returns
  2xx`, case-folding handles it; punctuation differences are NOT normalized away,
  so the invariant text must be authored to match what the agent reports. Unknown
  until a live run; step 7 surfaces it.
- Two coexisting matcher paths (structured + free-text) until all seeds migrate.
  Acceptable per ADR-0030 decision 4 but doubles the matcher test surface.
- Interaction with the merge/projection: a structured signal's `value` is still a
  string in the store; the projection groups by `(kind, type, value)`. Need to
  confirm the structured predicate string is stable across seeds (it is the
  abstract template, not the bound instance), so grouping is unaffected. Verify
  in step 5; flag if the projection needs awareness of the predicate.

## Open decisions (Pablo must approve before code)

### A. Field shape: one `value_predicate` string, or a `value` + `slots` pair? (RECOMMEND: single `value_predicate` template string)

The slots can be expressed two ways. (1) Keep the slot markup INLINE in one new
optional `value_predicate` string (`"... id equals {campaign_id}"`,
`"/Box/Editor/{numeric}"`), parsed by the decision-3 module. (2) Keep `value` as
prose and add a separate `slots` list naming each variable.

Recommendation: option (1), a single inline `value_predicate` string. It matches
the EXISTING `risks.trigger.expect` convention (which already inlines `{username}`
/ `{tag}`), keeps the model change to one optional field, and keeps the invariant
and its slots in one readable place. Option (2) splits one fact across two fields
and invites them drifting apart.

Pablo's call needed because it sets the data-model shape that every later seed and
the teach skill depend on.

### B. Does a structured predicate REPLACE or SUPPLEMENT the prose `value`? (RECOMMEND: supplement; `value` stays the human-readable prose, `value_predicate` is the checkable form when present)

If `value_predicate` is present, the matcher uses it; `value` stays as the
human-readable description (and the projection grouping key). This keeps `value`
required (no schema break) and gives a reader prose plus a machine a predicate.
The alternative (predicate replaces prose) would make `value` optional, a bigger
schema change touching the required list and the grouping key.

Recommendation: supplement. Pablo's call because it affects whether `value` can
ever be absent and how the projection groups.

### C. Migration scope for THIS change: re-seed one goal, or all dogfood goals? (RECOMMEND: re-seed exactly ONE goal as the worked example; leave the rest free-text)

ADR-0030 decision 4 keeps the free-text path matching existing seeds, so a bulk
re-seed is NOT required to ship. Recommendation: re-seed only `create-welcome-popup`
(the live failure) as the proof, leave every other seed free-text, and let
re-seeding the rest be incremental human teach work. Bulk re-seed is a separate,
larger task with its own review.

Pablo's call because it sets how much seed churn lands in this PR.

### D. Slot shape vocabulary scope: ship `numeric` + `uuid` only, or also bare `{slot}`? (RECOMMEND: ship bare `{slot}` + `numeric` + `uuid`, nothing more)

ADR-0030 decision 5 names `numeric`, `uuid`, and bare `{slot}` (presence-only).
Recommendation: ship exactly those three; defer any richer shape (regex,
enum, semver) to a future ADR when a real SUT needs it, to keep the validator
deterministic and small (the `trigger_validator` discipline).

Pablo's call because adding a shape later is additive but removing one is a break.

## Note on sequencing (restated for the reviewer)

This is the most fundamental change of the current batch: it alters the matcher
and the data model at the oracle-sacred core. Land it AFTER ADR-0029
(oracle self-certification) merges and rebase onto that tip, because both touch
the matcher/verdict region of `regression.py`. Keeping them in series keeps each
diff small and each guarantee (no-self-certification, no-false-pass) reviewable on
its own.
