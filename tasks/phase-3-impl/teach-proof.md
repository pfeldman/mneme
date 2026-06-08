# Step 11: teach end-to-end proof (ADR-0022)

The highest-risk Phase 3 item: the teach loop driving a REAL browser while
blocking on a human answer, emitting a legitimate ADR-0005 human seed, with
credentials never persisted. This is the live proof the handoff demanded.

## Setup

- SUT: `experiments/ui-mutation/testapp.py` on `http://127.0.0.1:8000`
  (`GET /_reset` first for a clean run).
- Brain: the local Claude Code session (the ADR-0019 local-brain path, no API
  key), driving the browser through a Playwright MCP (`browser_*` tools).
- Human in the loop: Pablo (the confirming human, `source_id = pablo`).
- Intent typed (the whole input a user gives): `a user can log in`.

## The run (as a user would experience `/praxis:teach a user can log in`)

1. Open the app root; follow the `login` nav link to the sign-in form.
2. The form needs a credential. In real use this is a typed `credential`
   prompt or a read from `.praxis.secrets`; for the proof a throwaway test
   email and password drove the browser. The secret is the browser's INPUT,
   never the knowledge's output.
3. Fill the form, submit. The app navigates to the post-login Home page.
4. Observe two diverse success signals (ADR-0005 diversity):
   - behavioral: a `Sign out` control is present on the post-login page.
   - network: `POST /session` returns 2xx and sets a session cookie.
5. Typed `confirmation` prompt to the human; Pablo confirmed the reached state
   is the intended success. That affirmative confirm is the ADR-0005 human
   seed act (the dual end condition: happy-path-observed AND human-confirm).

## Emitted knowledge (the seam output)

The session built the seed through `praxis.teach.session.TeachSession`
(`build_seed` then `finish`) and wrote the goal YAML. The emitted oracle is a
human seed (`source_type: human`, `source_id: pablo`), two signals of
different type, and only the abstract ADR-0017 `auth_state` posture:

```yaml
schema_version: '0'
goal_id: login
goal: a user can log in
target:
  app: acme-test
  environment: local
  observed_app_versions:
  - local
success_signals:
- type: behavioral
  value: a Sign out control is present on the post-login page
  provenance:
    source_type: human
    source_id: pablo
    observed_app_version: local
    observation_count: 1
  confidence: 1.0
  status: believed
- type: network
  value: POST /session returns 2xx and sets a session cookie
  provenance:
    source_type: human
    source_id: pablo
    observation_count: 1
  confidence: 1.0
  status: believed
auth_state:
  authenticated: true
  scope: user
```

## Verification (the contract held)

- `TeachSession.finish` returned `converged = True` and wrote
  `.praxis/knowledge/login.knowledge.yaml`.
- `assert_no_credential_leak(seed)`: PASS (no secret crossed into the emitted
  knowledge).
- The throwaway password literal is NOT in the file (`present? False`).
- The login email literal is NOT in the file (`present? False`).
- The raw cookie value is NOT in the file (`present? False`).
- `pydantic load`: PASS; `jsonschema` validate against
  `schema/knowledge.schema.json`: PASS.
- The success oracle source type is `human` only (the legitimate ADR-0005
  first-oracle seed path), not self-certified by agent count.

## What this proves and what it does not

Proven: the teach loop runs the documented protocol against a real browser
with a human confirm, emits a schema-valid human-seeded goal, records only the
abstract auth posture, and never persists the credential. The novel
human-in-the-loop-driving-a-live-browser path works end to end.

Not in scope here: the navigation-hint and not-converged branches were not
exercised in this happy-path run (they are covered by `tests/test_teach_session.py`);
a believed-goal re-teach producing a contested candidate is likewise unit-tested,
not re-run live here.
