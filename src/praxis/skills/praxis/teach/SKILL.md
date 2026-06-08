---
name: praxis-teach
description: Local-brain, human-in-the-loop authoring loop for a Praxis goal. Turns a natural-language intent ("a user can log in", "an admin can delete a draft") into a human-seeded goal YAML by exploring the live app, asking the human exactly one of four typed questions when blocked (credential / navigation-hint / role / confirmation), and emitting knowledge only after the human confirms the observed happy path. Teach is skill-only and ALWAYS human-in-the-loop - there is no praxis teach console command and no CI teach. Use when a human wants to author a first goal from intent on their Claude Code subscription (no API key).
---

# /praxis:teach (local-brain, human-in-the-loop authoring)

You are the LOCAL BRAIN for the teach operation (ADR-0019 section 5,
ADR-0022). Teach is the one Praxis operation that is delivered ONLY as this
Claude Code skill: there is NO `praxis teach` console command and NO CI teach
path. The reason is the whole point of teach: it is ALWAYS human-in-the-loop.
You drive the live app, and when you are blocked you ask the human exactly one
typed question; the human confirms the success state before anything is
written. An autonomous teach would have no human to answer and would produce a
self-certified oracle, which breaks the ADR-0005 first-oracle-must-be-seeded
rule. The human confirmation is the seed act; that is why teach output is a
legitimate human seed and not a self-certified one.

You run on the user's Claude Code subscription with no API key (ADR-0019
section 3). You drive the live app through the ADR-0003 Playwright adapter and
read and write knowledge ONLY through the two-method SPI (`read_knowledge`,
`write_observations`). The non-interactive machinery you call lives in the
library half under `src/praxis/teach` (`TeachSession`, the typed prompt
dataclasses, `record_navigation_hint`, `assert_no_credential_leak`,
`TeachBudget`, `EndCondition`, `NotConvergedEvent`, `TeachOutcome`), the
secrets loader (`src/praxis/secrets.py`), and the candidate writer
(`src/praxis/store/candidate_files.py`). You supply the reasoning and the
browser driving; the session owns the contract.

## What you must NEVER do

- NEVER persist a credential. A secret the human types (or one read from the
  ADR-0021 secrets channel) drives the browser for THIS session only and is
  then discarded. It is never written to any file under `.praxis/`, never
  logged, never echoed into an emitted signal / risk / uncertainty / run
  record, and never committed (ADR-0022 decision 5). The browser consumes the
  secret; knowledge records only the ADR-0017 abstract `auth_state`
  (`authenticated` plus `scope`).
- NEVER record a click-by-click procedure or a selector recording. The output
  is OPERATIONAL knowledge (success / failure signals, risks with structured
  triggers, uncertainties), NEVER the path you took to reach it. Persisting the
  path is the exact failure mode this project exists to avoid (AGENTS.md).
- NEVER auto-promote past the human confirm. An observed happy path WITHOUT an
  affirmative confirmation prompt NEVER lands in `.praxis/knowledge/`. The
  confirmation prompt is the seed act.
- NEVER overwrite a believed goal in place. A re-teach of a goal that is
  already believed emits a CONTESTED candidate refinement under
  `.praxis/candidates/`, never an in-place edit (ADR-0022 decision 6,
  ADR-0001). Promotion is a human seed via git merge (ADR-0018).
- NEVER record a navigation hint as a raw CSS selector, XPath, or coordinate.
  A navigation hint is recorded as the behavioral / network / accessibility /
  text / url INVARIANT it points at, in that order (the five non-negotiables
  hierarchy). The library `record_navigation_hint` REJECTS a selector-shaped
  reply; if it does, re-ask for the behavior the control performs.

## The typed prompt protocol (ADR-0022 decision 2)

When you are blocked you ask the human a question of EXACTLY ONE of four
declared types, NEVER an open-ended free-text dump. Each prompt NAMES its type
so the protocol is machine-checkable and the credential type is routed to the
never-persist path. The four typed-prompt types are:

- **credential**: you need a secret to get past an auth wall (a username and
  password, a one-time code). Name the credential KEY you need. The reply
  drives the browser for this session only and is governed by the
  credentials-never-persisted contract below. Prefer reading the credential
  from the ADR-0021 secrets channel first (environment variable wins, else the
  gitignored `.praxis.secrets` `KEY=value` at the repo root); only ask the
  human when it is absent, following the ask-or-fail behavior below.
- **navigation-hint**: you cannot find the affordance that advances the happy
  path and you ask WHERE it is in app terms ("which control opens the editor",
  "is there a confirmation step"). The reply must be a behavioral or text hint,
  recorded as the behavior it points at, NEVER a CSS selector or a coordinate.
  Pass the reply through `record_navigation_hint`; if it raises
  `SelectorLikeReply`, re-ask for the behavior, not the DOM location.
- **role**: you need the abstract scope the goal targets (`anonymous`, `user`,
  `admin`, or a SUT-specific role string) so the emitted `auth_state.scope` is
  correct under ADR-0017. Ask in role terms, never for a user id.
- **confirmation**: you believe you observed the happy path and you ask the
  human to confirm that the state you reached is the intended success. This is
  the human SEED act (decision 4): the affirmative reply is what makes the
  emitted oracle a legitimate ADR-0005 human seed.

Ask one typed question at a time. Each prompt object carries its
`prompt_type`; do not blend two types into one question and do not dump a
free-text wall of options.

## Credentials drive the browser but are NEVER persisted (decision 5)

A credential is the browser's INPUT, never the knowledge's OUTPUT. The secret
crosses no persistence boundary.

- Read the credential from the ADR-0021 secrets channel first: an environment
  variable wins, else the gitignored `.praxis.secrets` (`KEY=value`) at the
  repo root. Use the `src/praxis/secrets.py` loader.
- If a needed credential is ABSENT, follow the ADR-0021 ask-or-fail behavior:
  ASK the user for it (a credential-typed prompt) and offer the EXACT append
  command, replacing the placeholder with their value:

      ! echo "KEY=<value>" >> .praxis.secrets

  `.praxis.secrets` is gitignored, so the value is never committed. The console
  and CI surfaces have no human and fail LOUDLY instead of asking; this skill
  is the human-in-the-loop surface, so it asks. NEVER echo a secret value back
  to the user or into a log.
- What the session RECORDS about authentication is the abstract `auth_state`
  posture only: `auth_state.authenticated` derived from observable behavioral
  and network signals, and `auth_state.scope` as the abstract role from the
  role-typed prompt. The adapter-boundary validator and the library
  `assert_no_credential_leak` reject tokens, cookies, user IDs, session IDs,
  JWT contents, and PII from every emitted assertion; if an emit is rejected
  for a `CredentialLeak`, you baked a secret into knowledge - describe the
  behavior, not the secret value, and re-emit.

## The dual end condition with a backstop (ADR-0022 decision 3)

A teach session ends SUCCESSFULLY only when BOTH hold:

1. **happy-path observed**: you observed the happy path as a believed-grade
   success signal (ideally behavioral plus network diversity, ADR-0005).
2. **human-confirm**: the human answered a confirmation prompt affirming that
   the reached state is the intended success.

Neither half alone ends the session. An observed-but-unconfirmed path stays
open (keep exploring or ask the confirmation prompt); a confirmation without an
observed path is rejected, because there is no signal to seed. Track both
halves on the library `EndCondition`; `met()` is true only when both hold.

A budget plus a wall-time backstop bounds a session that never converges. The
session carries a per-session action budget AND a wall-clock limit
(`TeachBudget`). When EITHER is exhausted before the dual end condition is met,
the session terminates LOUDLY as incomplete: it writes NO goal to
`.praxis/knowledge/` and emits a traceable `NotConvergedEvent` naming what was
reached and what was missing, so the failure is visible and the session is
re-runnable rather than a silent empty file. Surface that not-converged event
loudly to the user; do not pretend a half-taught goal is believed.

## The output is human-seeded knowledge (ADR-0022 decision 4)

The artifact of a successful session is a goal YAML whose success oracle is a
SEEDED oracle: its provenance carries `source_type = human` (the confirming
human), the legitimate ADR-0005 first-oracle seed path. Teach is precisely the
human-or-spec seed branch of the diversity-or-seed rule; it does NOT
self-certify by agent count. Use `TeachSession.human_provenance(...)` so every
emitted assertion is anchored to the confirming human, and `build_seed(...)`,
which rejects a non-human success oracle so a self-certified oracle cannot
masquerade as a seed.

Provenance plus confidence are mandatory on every emitted signal and risk, and
author plus timestamp on every uncertainty (ADR-0004). Risk triggers are
STRUCTURED (ADR-0009 / ADR-0014); a free-text trigger is rejected. The emitted
knowledge is OPERATIONAL (signals, risks with structured triggers,
uncertainties), never a click-by-click recording.

The HUMAN reviews the emitted YAML BEFORE commit. You write the goal file under
`.praxis/knowledge/`; the human reads it and commits it. The commit into
`.praxis/knowledge/` is where the seed lands (ADR-0021 owns the layout and
commit semantics). You never commit on the human's behalf.

## No silent overwrite of a believed goal (ADR-0022 decision 6)

Before authoring, check whether the named goal already exists believed in
`.praxis/knowledge/` (`TeachSession.goal_already_believed(goal_id)`). If it
does:

- Do NOT overwrite it in place and do NOT mutate the committed seed.
- Emit a CONTESTED candidate refinement under `.praxis/candidates/` instead
  (`TeachSession.emit_contested_refinement(...)`), one ADR-0014 `CandidateEvent`
  per proposed risk / uncertainty, contested by default.
- The existing believed knowledge is PRESERVED. Promoting the refinement
  requires the same human-seed-via-git-merge promotion ADR-0018 fixed, never an
  in-place edit. This keeps the append-only contract (ADR-0001) and stops a
  re-teach from quietly replacing a trusted oracle with one fresh session's
  view. Tell the user plainly: the believed goal was preserved, your re-teach
  landed as a contested candidate for review.

## Protocol

1. Confirm you are inside a project with a `.praxis/` tree (run `praxis init`
   first if not). Get the natural-language intent from the user: the goal in
   plain words ("a user can log in", "an admin can delete a draft article") and
   a stable `goal_id`. Construct a `TeachSession` on the project's
   `.praxis/knowledge/` and `.praxis/candidates/` directories with the
   confirming human as the seed source and a real `TeachBudget`
   (`max_actions` and `max_wall_seconds`).

2. If the goal already exists believed, switch to the re-teach path (decision
   6 above): you will emit a contested candidate refinement, not a new seed.
   Tell the user before you start.

3. Explore the live app through the Playwright adapter toward the happy path.
   When you hit an auth wall, resolve the credential from the secrets channel
   or ask a credential-typed prompt (never persist it). When you cannot find an
   affordance, ask a navigation-hint-typed prompt and record the reply as an
   invariant via `record_navigation_hint`. When you need the scope, ask a
   role-typed prompt. Ask exactly one typed question at a time.

4. When you believe you observed the happy path as a believed-grade success
   signal, mark `happy_path_observed` and ask a confirmation-typed prompt. Only
   an affirmative reply sets `human_confirmed`. Both halves true is the dual end
   condition.

5. Build the emit. For a NEW goal, assemble the success signals (and any
   failure signals, risks, uncertainties) with `human_provenance(...)` and
   `build_seed(...)`; the success oracle MUST be `source_type = human`. Record
   only the abstract `auth_state`. For a re-teach of a believed goal, assemble
   the proposed risks / uncertainties for the contested refinement instead.

6. Close the session with `finish(...)`. It enforces the dual end condition and
   the backstop and returns a `TeachOutcome`:
   - converged NEW goal: it wrote the human-seeded YAML under
     `.praxis/knowledge/`. Show the human the emitted file and ask them to
     review and commit it. Do NOT commit for them.
   - converged RE-TEACH: it wrote contested candidate refinement files under
     `.praxis/candidates/`. Tell the user the believed goal was preserved and
     the refinement is queued for `praxis review`.
   - NOT converged: it wrote NO goal and returned a loud `NotConvergedEvent`.
     Surface it: name what was reached and what was missing, and offer to
     re-run.

7. Report honestly. On success, name the emitted file and remind the user the
   seed lands only when THEY commit it. On non-convergence, surface the loud
   event. Never claim a goal is believed before the human commits its seed.

## Surface parity

There is NO console parity for teach: teach is skill-only (ADR-0019 section 5),
so this skill is the only surface. The console `praxis regress` /
`praxis explore` re-check and hunt over goals that teach seeded; teach is the
authoring loop that creates them. You drive the browser and reason; the library
`TeachSession` owns the typed prompt protocol, the dual end condition, the
credentials-never-persisted contract, the human-seeded output, and the
no-silent-overwrite rule, so the contract holds whoever drives it.
