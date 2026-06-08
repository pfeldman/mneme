---
type: task-plan
ticket: null
mode: context
created: 2026-06-08
status: approved
approval_notes: |
  Pablo approved 2026-06-08. Open decisions 1 and 2 resolved to the proposed
  defaults. Open decision 3 resolved: do NOT accept the ADR yet. ADR-0026 stays
  Proposed and gets no CHANGELOG done-entry until the live real-app proof
  verifies it ("hasta que no verifiquemos, no marcamos"). Step 7 (accept +
  CHANGELOG) is therefore DROPPED; the loop runs Steps 1-6 only.
---

# Plan: implement ADR-0026 - persistent authenticated-session reuse

## Brief

Implement the ADR-0026 decision (merged to main this session): save and reuse
the Playwright authenticated session so a real app whose login needs 2FA is
testable without passing 2FA every run. See `task-brief.md` for the synthesized
brief. The ADR is the spec; this task builds the LIBRARY half (fully testable,
no browser) plus the skill wiring; the live browser-side proof against a real
app is a separate run.

## Pre-flight observations (grounded this session)

- `src/praxis/adapters/playwright.py` today is a script RECORDER/emitter, not a
  live driver. The real browser is driven by the brain (Claude Code via the
  Playwright MCP), so the browser-side storageState export/import lives in the
  SKILL, and the library owns the session STORE + the verdict. No live-driver
  adapter work is in scope.
- Verdicts: `RegressionVerdict` (PASS / FAIL / UNCERTAIN) per goal and
  `AggregateVerdict` (OK / REGRESSED / STALE / ERROR) at the aggregate
  (`src/praxis/runner/regression.py`). AUTH-EXPIRED is a NEW value on both, with
  classification ahead of FAIL/REGRESSED.
- `src/praxis/secrets.py` has the env-wins-over-file loader
  (`get_credential`, `MissingCredential`) to MIRROR for the session.
- `praxis init` already gitignores `.praxis/runs/` and `.praxis.secrets`
  (`_cmd_init` in `src/praxis/cli/main.py`); the session path is added the same
  way.

## Steps

- [ ] Step 1: Session store (the secret channel for the session).
  Add `src/praxis/auth_session.py`: save/load a Playwright storageState JSON
  keyed by role (ADR-0017 scope), on the ADR-0021 channel. Resolution order
  mirrors `secrets.get_credential`: an environment variable / CI runner secret
  (for example `PRAXIS_AUTH_STATE_<ROLE>`) WINS over a gitignored local file
  (proposed `.praxis.auth/<role>.json` at the repo root, a sibling of
  `.praxis.secrets`). A `MissingSession`-style absence is ask-or-fail by
  surface. The module never echoes the session content to stdout or a log. The
  session is never written into `.praxis/knowledge`, `.praxis/candidates`, or
  any committed file.
  - Files: `src/praxis/auth_session.py` (new), `tests/test_auth_session.py` (new)
  - Verification: save/load round-trip per role; env / CI secret beats the file;
    a missing session raises the named error; no test observes the session value
    echoed; the file path is outside the committed `.praxis/` tree; verify.sh
    ALL GREEN.

- [ ] Step 2: `praxis init` gitignores the session path before any write.
  `_cmd_init` appends the session path (proposed `.praxis.auth/`) to the repo
  `.gitignore` idempotently, alongside `.praxis/runs/` and `.praxis.secrets`,
  so the session can never be committed by accident. Update the init report
  line and the init-layout tests.
  - Files: `src/praxis/cli/main.py`, `tests/test_init_layout.py`
  - Verification: a fresh `praxis init` gitignores the session path; re-init
    does not duplicate the line; the path is ignored BEFORE any session write;
    verify.sh ALL GREEN.

- [ ] Step 3: AUTH-EXPIRED verdict + classification.
  Add AUTH_EXPIRED to `RegressionVerdict` and `AggregateVerdict`. The classifier
  routes a run that observed an auth wall / unauthenticated-when-an-authenticated
  -scope-was-expected to AUTH-EXPIRED BEFORE FAIL/REGRESSED/STALE (the run input
  carries an `authenticated` flag the brain reports; see Open decision 2). It is
  NOT a regression and NOT stale knowledge. Loud: a distinct non-OK aggregate
  outcome that fails the run with a named role/goal, never a false REGRESSED,
  never silent green.
  - Files: `src/praxis/runner/regression.py`, `tests/test_regress_aggregate.py`
  - Verification: a logged-out run (auth wall) classifies AUTH-EXPIRED, not
    FAIL/REGRESSED; an authenticated run with a fired failure signal still
    classifies REGRESSED; AUTH-EXPIRED is a loud non-OK aggregate outcome;
    auditor scenarios stay excluded (ADR-0009); verify.sh ALL GREEN.

- [ ] Step 4: AUTH-EXPIRED in the aggregate report + CLI exit code.
  Surface AUTH-EXPIRED distinctly in the markdown aggregate report and in the
  console exit-code contract (a named third non-OK outcome, distinct in the
  report from REGRESSED and STALE). One AUTH-EXPIRED goal fails the run loudly,
  naming the role whose session expired.
  - Files: `src/praxis/runner/report.py`, `src/praxis/cli/main.py`,
    `tests/test_regress_aggregate.py`
  - Verification: the report names AUTH-EXPIRED goals and the expired role
    distinctly; the run exits non-zero on an AUTH-EXPIRED goal; verify.sh
    ALL GREEN.

- [ ] Step 5: Skill seams + skill text for save/load/re-auth.
  Add the library seams the skills call: a save hook (teach saves the session
  after a confirmed login, the 2FA bootstrap) and a load hook (regress/explore
  fetch the saved session for the brain to inject before driving the browser),
  both through `auth_session.py`. Update the three SKILL.md: `/praxis:teach`
  exports the storageState via the MCP after login and saves it; `/praxis:regress`
  and `/praxis:explore` load it before the run and, on AUTH-EXPIRED, the skill
  asks the human to re-authenticate (pass 2FA once) and re-saves, while the
  console / CI surface fails loud. Credentials and the 2FA code are never
  persisted (ADR-0022 decision 5); only the session secret and the abstract
  auth_state are stored.
  - Files: a seam in `src/praxis/teach/` or `auth_session.py`,
    `src/praxis/skills/praxis/teach/SKILL.md`,
    `src/praxis/skills/praxis/regress/SKILL.md`,
    `src/praxis/skills/praxis/explore/SKILL.md`,
    `tests/test_packaging.py` (skills still ship/scaffold) + seam tests
  - Verification: the seams save/load through the channel without leaking a
    secret; the skills still resolve from package data and scaffold; the skill
    text encodes the save-after-login, load-before-run, and re-auth-on-
    AUTH-EXPIRED protocol; verify.sh ALL GREEN.

- [ ] Step 6: Docs - session reuse + AUTH-EXPIRED.
  Add a short docs section (a page or an addition to an existing examples page)
  on session reuse and the AUTH-EXPIRED outcome, and add the session secret to
  the example CI workflow (`docs/examples/ci/praxis-regress.yml`) as a runner
  secret (a human refreshes it; note the email-2FA-vs-TOTP cost). Keep the
  `mkdocs build --strict` output clean.
  - Files: `docs/examples/ci/praxis-regress.yml`, a docs page (new or extended),
    `mkdocs.yml` nav if a new page
  - Verification: the `mkdocs build --strict` build is clean; the session is
    shown as a runner secret, never committed; verify.sh ALL GREEN.

DROPPED - Step 7 (Accept ADR-0026 + CHANGELOG): removed per Pablo's call. The
ADR stays `Proposed` and gets no CHANGELOG done-entry until the live real-app
proof verifies it. We do not mark it done before we have seen it work.

## Pre-conditions

- Branch: `adr-0026-auth-session-impl` off `main`.
- Baseline: `bash verify.sh` ALL GREEN.
- Do NOT activate the deferred schema fields `states` / `paths` or a `refuted`
  status (Phase 1.5).
- The live browser-side proof against a real app (export/import a real session,
  reuse it across runs) is a SEPARATE run needing a browser MCP and the real
  app, like the teach proof; it is not a step here.

## Risks / unknowns

- The end-to-end session reuse can only be proven live (browser-side
  storageState export/import via the MCP). This task builds and tests the
  library half (store + verdict) and the skill protocol; the live proof is
  deferred to a real-app run.
- AUTH-EXPIRED detection depends on the run reporting whether it hit an auth
  wall (Open decision 2). If the brain does not surface that, the classifier
  cannot distinguish AUTH-EXPIRED from UNCERTAIN; the seam must make the
  authenticated flag explicit in the observation payload.

## Open decisions (resolved)

1. RESOLVED to the default: a gitignored `.praxis.auth/<role>.json` local file
   plus an env / CI secret `PRAXIS_AUTH_STATE_<ROLE>` that wins over it.
2. RESOLVED to the default: the run observation payload carries an explicit
   `authenticated` boolean (the brain reports whether it hit a login wall); the
   classifier reads it to route AUTH-EXPIRED ahead of FAIL/REGRESSED.
3. RESOLVED: do NOT accept the ADR yet. It stays `Proposed` until the live
   real-app proof. Step 7 is dropped.
