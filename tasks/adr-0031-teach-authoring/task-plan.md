---
type: task-plan
ticket: null
mode: freetext
adr: 0031
created: 2026-06-09
status: pending-approval
---

# Plan: ADR-0031 teach-UX follow-up (author structured checks end to end)

Goal: make structured checks usable through the NORMAL authoring + reporting
flow, not by hand-editing YAML. Teach must author a typed `check` for relational
/ after-action facts; the regress emit envelope must carry the `observed`
payload; then re-author `delete-a-campaign` through teach and prove the false
REGRESSED becomes a real PASS live.

Why this exists: ADR-0031 added `check` to the model + matcher + read path, but
deferred the teach authoring UX. As shipped, a check can only reach a seed by
hand-editing YAML, which is exactly the off-flow authoring the project avoids.
This closes the authoring + reporting halves so the feature is real end to end.

Conventions (this repo): branch `adr-0031-build` already carries the ADR-0031
code + the console running-line UI (not yet PR'd); this work continues on it so
the feature lands as one cohesive ADR-0031 PR. One commit = one logical change,
plain one-line messages, no Co-Authored-By; keep `bash verify.sh` ALL GREEN
before each commit; PR + merge on Pablo's OK.

## Steps

- [x] Step 1: Let the regress emit ENVELOPE carry the `observed` payload
  - Update `_HEADLESS_PREAMBLE` in `cli/claude_brain.py`: the documented JSON
    envelope an agent emits gains an OPTIONAL `"observed"` object per
    observation, so a `claude -p` regress run reports the raw structured data a
    check needs (before/after counts; identifier + membership). The per-goal
    prompt already asks for it (ADR-0031 step 6); this aligns the envelope so
    the two never disagree.
  - Files: `src/praxis/cli/claude_brain.py` (+ a test asserting the preamble
    documents `observed`).
  - Verification: a regress agent now has a contract slot to report a check's
    data; without it the check would fail closed (a false REGRESSED) even with a
    correct seed.

- [ ] Step 2: Teach the teach skill to AUTHOR structured checks
  - Extend `src/praxis/skills/praxis-teach/SKILL.md` with a section on
    structured checks: when a success/failure fact is RELATIONAL (a count delta,
    "exactly one fewer") or an AFTER-ACTION ABSENCE (an id that disappears),
    author a typed `check` (`list_count_delta` / `element_membership`) instead of
    free prose, the SAME way teach already authors a structured risk `trigger`
    rather than free text. Include the concrete `delete-a-campaign` YAML shape
    (the count-delta signal and the membership signal) and the rule that the
    invariant lives in the typed fields, the per-run id stays an abstract slot
    (never a concrete number in knowledge).
  - Keep teach's existing discipline intact: a check is still seeded
    human-confirmed; a relational fact still needs the human to confirm the
    before/after they saw; `praxis learn --from-file` still validates against the
    schema (which now accepts `check`).
  - Files: `src/praxis/skills/praxis-teach/SKILL.md`.
  - Verification: a light test asserting the skill text names the check kinds, so
    the guidance cannot silently regress out.

- [ ] Step 3: Note structured-check confirmation on the regress skill surface
  - Small addition to `src/praxis/skills/praxis-regress/SKILL.md`: a check signal
    is confirmed by REPORTING THE DATA (the counts, the membership), never by the
    agent deciding it passed; the runner evaluates the check. Mirrors the
    grounding contract already there for typed signals.
  - Files: `src/praxis/skills/praxis-regress/SKILL.md`.
  - Verification: consistent with step 1's envelope; doc-only.

- [ ] Step 4: verify.sh ALL GREEN (the offline gate)
  - Run `bash verify.sh`; fix any drift. The skill files are markdown (no unit
    tests beyond the light guards in steps 1-2); this is the final offline sweep
    before the live proof.

- [ ] Step 5: Live proof - re-author delete-a-campaign via teach, prove PASS [VERIFICATION]
  - On the dogfood project (praxis-digioh, Pablo's subscription): run the teach
    flow for `delete-a-campaign` so it drives the archive happy path, observes
    the before/after list counts and the archived id's disappearance, the human
    confirms, and it WRITES the structured checks (not hand-edited). Install via
    `praxis learn`.
  - `rm -rf .praxis/runs`, run `praxis regress --goal delete-a-campaign` live ->
    expect a real PASS (was the false REGRESSED Pablo hit).
  - Confirm a planted regression (archive that removes nothing, or id still
    present) still fails loudly.
  - This is the live evidence for marking ADR-0031 Accepted. It runs on Pablo's
    machine + subscription; the seed change lives in praxis-digioh, not this repo.

## Pre-conditions
- Continue on branch `adr-0031-build` (carries the ADR-0031 code the live proof
  needs); keep verify.sh ALL GREEN before each commit.

## Risks / unknowns
- teach "does not silently overwrite a believed goal": a re-teach of the already-
  believed `delete-a-campaign` normally lands as a CONTESTED candidate, not a
  seed overwrite. For the live proof we want the believed seed to carry the
  structured checks. Decide at step 5 whether to (a) re-`praxis learn` the seed
  directly (explicit human re-seed of the same confirmed fact, new expression) or
  (b) route through the candidate path then promote. Lean (a): same human, same
  confirmed fact, only the EXPRESSION changes prose -> check; it is a refinement,
  not a new oracle. Flag to Pablo at the fork.
- The live run depends on the praxis-digioh stack + subscription; it is the only
  non-deterministic step.

## Open decisions
- PR structure: this continues `adr-0031-build`, so the eventual PR bundles the
  ADR-0031 build + the console running-line UI + this teach authoring. If Pablo
  wants the console UI split into its own PR, say so before the PR step.
- The believed-overwrite fork above (step 5 risk): resolve with Pablo at step 5.
