---
type: task-plan
ticket: null
mode: freetext
created: 2026-06-16
status: in-progress (Steps 1-6 done; Step 7 = live proof, needs Pablo)
---

# Plan: ADR-0036 console self-login from credentials (MFA-free)

Unblocks unattended CI: when the console `claude -p` brain would return
AUTH-EXPIRED for a precondition role with no usable session but credentials
resolve from the secret chain, drive a fresh username + password login (the
existing NL routine) instead of short-circuiting. No credentials -> AUTH-EXPIRED
unchanged. A 2FA / OTP prompt -> loud AUTH-EXPIRED naming role + env, never reads
an inbox. Credentials never persist; the console-minted session is not saved.

## Resolved decisions (Pablo, 2026-06-16)
1. Approach = preamble-driven (Option A): the agent does the login freeform via
   the Playwright MCP, reusing the existing `_HEADLESS_PREAMBLE` / teach NL
   routine. NO scripted-Playwright login, NO hardcoded selectors (ADR-0036
   decision 2; Option B rejected: couples Praxis to each app's login DOM).
2. Scope = ADR + task-plan first, then implement after review.

## Open decisions (for Pablo before/early in the loop)
- **2FA-blocked self-login verdict**: route as AUTH-EXPIRED (recommend, reuses
  ADR-0027 decision 3 headless-2FA-block routing + loud non-zero exit, evidence
  names role + env) vs a distinct ERROR. Recommend AUTH-EXPIRED.
- **Partial credentials** (only username OR only password present for the role):
  treat as "not present" and route to AUTH-EXPIRED with a hint naming the missing
  key (recommend, keeps the run from crashing mid-fleet) vs a loud config ERROR.
  Recommend AUTH-EXPIRED-with-hint.
- **`user` unsuffixed special-case**: confirm role `user` -> `APP_USERNAME` /
  `APP_PASSWORD` (no suffix), every other role suffixed `_<ROLE_UPPER>`. Recommend
  yes (matches the spec + back-compat with the only keys that exist today).
- **Console-minted session persistence**: do NOT persist to
  `.praxis.auth/<env>/<role>.json` (recommend, matches the CI "log in fresh each
  run" posture; autonomous persistence stays the human teach seed, ADR-0026 d7).
  Confirm we are NOT adding a `--save-session` warm-cache in this ADR.

## Steps

- [x] **Step 1: Commit the already-written ADR-0036 + README index row (on disk, uncommitted)**
  - Files: `docs/adr/0036-console-self-login-from-credentials.md` (new), `docs/adr/README.md` (new 0036 row).
  - Note: commit only the two ADR files (`git add docs/adr/0036-*.md docs/adr/README.md`).
  - Verification: `git log` shows the commit; `bash verify.sh` ALL GREEN (docs-only).

- [x] **Step 2: Add the per-role credential-key helper + presence check (decision 3)**
  - Files: `src/praxis/secrets.py` (add `credential_keys_for_role(role) -> tuple[str, str]` returning `(APP_USERNAME[_<ROLE>], APP_PASSWORD[_<ROLE>])`, with `user` unsuffixed; add `credentials_present_for_role(role, *, environment, repo_root=None, secrets_path=None, environ=None) -> bool` that returns True only when BOTH keys resolve via the existing `get_credential` chain, swallowing `MissingCredential`, and NEVER returning or logging a value), `tests/test_secrets.py`.
  - Detail: pure name mapping + presence; reuses `get_credential` so env > overlay > base and the ADR-0035 env scoping are NOT re-implemented. The presence check reads the value internally to confirm it resolves but returns only a bool; no value crosses out. `user` -> unsuffixed; `mcp_readonly` -> `APP_USERNAME_MCP_READONLY` / `APP_PASSWORD_MCP_READONLY`; uppercase the role with the same rule `auth_session.env_var_name` uses.
  - Verification: `pytest tests/test_secrets.py -q` (user unsuffixed; other role suffixed; present iff both keys resolve; overlay wins; absent both -> False; partial -> False per the open decision); `bash verify.sh` ALL GREEN.

- [x] **Step 3: Add a self-login outcome to `resolve_storage_state` (decisions 1, 4)**
  - Files: `src/praxis/cli/claude_brain.py` (`StorageStateResolution` gains a `self_login_role: str | None` field, mutually exclusive with `path` and `missing_role`; `resolve_storage_state` gains an injectable `credentials_present` callback defaulting to `secrets.credentials_present_for_role` so it stays unit-testable without a real `.praxis.secrets`). When the resolution WOULD be `missing_role` (no env var, no file, or the env session is malformed), call `credentials_present(role, environment=environment)`: if True -> return `StorageStateResolution(self_login_role=role)`; if False -> `StorageStateResolution(missing_role=role)` (unchanged AUTH-EXPIRED).
  - Detail: the `role_for_session_reuse` gate is unchanged (anonymous / `being_tested` still get an empty resolution, no self-login forced on them). Update the `StorageStateResolution` docstring to name the fourth shape. The credential VALUES never enter this module; only presence (a bool) and the role NAME do.
  - Verification: extend `tests/test_claude_brain.py` (no session + creds present -> `self_login_role` set, `missing_role`/`path` None; no session + no creds -> `missing_role` set, byte-identical to today; env / file session present -> unchanged; anonymous / being_tested -> empty resolution, never self-login); `bash verify.sh` ALL GREEN.

- [x] **Step 4: Wire the self-login path through `brain()` + a key-naming directive (decisions 1, 3, 5)**
  - Files: `src/praxis/cli/claude_brain.py` (`brain()` inner fn), small preamble addendum constant.
  - Detail: in `brain()`, after `resolve_storage_state`: `missing_role` -> `_auth_expired_observations` (unchanged); `self_login_role` -> drive the browser with NO `--storage-state` (a fresh login run) and prepend a SELF-LOGIN directive to the prompt naming the exact credential keys for the role (`credential_keys_for_role`), instructing the agent to open the app's login (the "App under test:" base_url is already in the TASK section, ADR-0035 d3), type those keys' values, and, if a 2FA / one-time-code prompt appears, STOP with `authenticated: false` (the existing 2FA-stop, now naming role + env). The generic non-self-login preamble stays BYTE-IDENTICAL (ADR-0035 byte-identity bar): the directive is appended ONLY on the self-login path. No `--storage-state` MCP synth on this path (no session to inject).
  - Detail (redaction): the directive names KEY names only, never values; reuse the preamble's "never write a credential/cookie/token/2FA code under `.praxis/`" sentence.
  - Verification: `tests/test_claude_brain.py` with monkeypatched `subprocess.run` (self-login path: no `--storage-state` in argv, the directive with the role's keys is in the prompt, success returns the right observation shape; the generic path's prompt is unchanged byte-for-byte); `bash verify.sh` ALL GREEN.

- [x] **Step 5: Confirm the 2FA-blocked + no-cred self-login both route loud (decision 5)**
  - Files: `tests/test_claude_brain.py` and/or `tests/test_regression_classify.py` (whichever owns the classifier path) - test-only, asserting existing wiring holds end to end.
  - Detail: a self-login run whose agent returns `authenticated: false` (2FA wall) classifies as AUTH-EXPIRED for the expected role (not a false green, not REGRESSED), evidence names role + env. A no-credential precondition role still classifies AUTH-EXPIRED. No production code change expected here; this step pins the contract so a later refactor cannot silently turn a 2FA wall green.
  - Verification: `pytest tests/test_claude_brain.py tests/test_regression_classify.py -q`; `bash verify.sh` ALL GREEN.

- [x] **Step 6: Note the console self-login in both skills + keep the credential wording consistent (decisions 2, 3, 5)**
  - Files: `src/praxis/skills/praxis-regress/SKILL.md`, `src/praxis/skills/praxis-explore/SKILL.md` (short note); optionally align `src/praxis/skills/praxis-teach/SKILL.md` credential section wording on the per-role keys.
  - Detail: short note that on the bare console path, when a precondition role has no usable session but credentials are set, the runner self-logs-in from `APP_USERNAME[_<ROLE>]` / `APP_PASSWORD[_<ROLE>]` against the selected env's base_url (MFA-free only); a 2FA prompt is a loud AUTH-EXPIRED stop; no credentials stays AUTH-EXPIRED; the teach interactive login is unchanged.
  - Verification: `pytest tests/test_packaging.py`; `bash verify.sh` ALL GREEN (markdown only).

- [ ] **Step 7 (LAST, NEEDS PABLO PRESENT - gates marking the ADR Accepted): live end-to-end proof against the MFA-free Digioh QA account**
  - Files: none tracked necessarily; the run happens against the real Digioh CI repo / account.
  - Detail: with the role's credentials in the secret chain and NO saved session, run a bare `praxis regress --env <env>` on an authenticated precondition goal: it must self-log-in and run the goal (no AUTH-EXPIRED), against the MFA-free, no-SSO account. Then the same run with neither session nor credentials -> still AUTH-EXPIRED. Confirm no credential appears in any artifact (knowledge / candidates / runs / reports / logs). After a green proof, flip ADR-0036 Status to Accepted as a final docs-only commit.
  - Verification: a real headless `claude -p` run self-logs-in and drives the goal end to end; the no-cred run is loud AUTH-EXPIRED; `bash verify.sh` ALL GREEN; the live proof recorded.

## Acceptance criteria (from the spec)
- Bare `praxis regress --env <env>` on an authenticated goal whose role has creds but NO saved session -> logs in automatically and runs (no AUTH-EXPIRED), MFA-free account.
- Same run with neither session nor credentials -> still AUTH-EXPIRED (unchanged).
- A 2FA / OTP prompt mid-login -> loud stop naming role + env; never reads an inbox.
- Credentials never appear in any artifact; redaction + `AuthStateLeakError` boundary still hold.
- Multi-env + multi-role honored: per-role credential resolution, per-env base_url for the login navigation.
- Exit-code contract otherwise unchanged (OK / REGRESSED / STALE / ERROR / AUTH-EXPIRED).

## Pre-conditions
- Branch: `adr-0036-console-self-login` (create + check out before Step 2).
- `bash verify.sh` ALL GREEN per commit. One commit = one logical change. No Co-Authored-By trailer.
- No new runtime dependency: stdlib only (the credential check + key mapping are pure Python over the existing channel).
- Do NOT touch the oracle, verdict classification, or candidate / promotion logic (ADR-0036 forbidden alternatives).
- Do NOT persist a console-minted session, read credential values into a logged surface, or hardcode login selectors.

## Risks / unknowns
- The self-login directive wording is agent-facing prose; the live proof (Step 7) is where "the agent reliably finds and fills the login form from base_url" is validated, the same live-proof risk ADR-0027 Step 9 carried.
- A genuinely MFA-enabled account silently passing some logins would mask the 2FA-stop; the test account must be confirmed MFA-free, and the 2FA-stop must stay loud when a code IS demanded (Step 5 pins it).
- Byte-identity of the generic (non-self-login) preamble must be preserved so undeclared / session-reuse runs are unchanged (ADR-0035 bar); the directive is append-only on the self-login path.
- Credential-presence check must not itself leak a value (returns bool only); confirm no value reaches stdout / logs even on the resolve path.
