# Real-app SUT: Conduit + auth_state (ADR-0016, ADR-0017)

Praxis used to be tested only against a small toy app that the same person who wrote the tests also wrote. That was useful for an early proof, but it left an obvious complaint open: "of course it works, you wrote the app". This feature moves the experiment onto a real open-source application called Conduit (a Medium-clone), so the same machinery now has to do its job on code nobody on the project authored. Alongside it, the knowledge schema gains one small field, `auth_state`, that records whether the current session is logged in and at what role, without ever storing tokens, cookies, user IDs, or any other per-session data.

## Why this exists

Before this feature, the only public evidence that Praxis worked was a run against `testapp.py`, a 200-line in-repo toy app. Any sceptic could (correctly) say "you tuned the agent and the toy to each other". The fix is to point the same machinery at a public app a stranger can stand up: Conduit, the RealWorld reference Medium-clone, brought up via one docker-compose script.

Conduit was not picked freely. ADR-0016 seals four selection criteria first (public and dockerizable under 30 minutes, at least three goal-shaped flows, public issue history, permissive license) and only then justifies the pick against them; Saleor is the named fallback. Once Praxis is running against a real app, it needs to know "am I logged in?" across multiple goals; the `auth_state` field makes that posture explicit instead of re-derived per goal, while a strict validator stops anyone from quietly storing the bearer token "for debugging".

## How to use it

You do not run a new CLI subcommand. The feature is two things bolted onto the existing experiment.

First, bring Conduit up locally (one time per machine):

```bash
bash experiments/regression_recall_real/setup/bring_up.sh
# --check    idempotent probe ("is it up?")
# --teardown stop and remove the stack
```

The script exits 0 once the Conduit API answers on `http://localhost:3000/api/tags`, or exits 3 if it cannot get there within 30 minutes (the ADR-0016 ceiling).

Second, write `auth_state` into any goal's knowledge file. It is a two-field block:

```yaml
auth_state:
  authenticated: true     # the projection believes the session is logged in
  scope: user             # abstract role: anonymous | user | admin | <SUT role>
```

`scope` must be `null` when `authenticated` is `false`. The recommended scope values are `anonymous`, `user`, and `admin`; an app under test (a SUT, "system under test") can register additional role strings by using them in its own knowledge files. The pydantic validator rejects any `scope` value that looks like a token, cookie name, user ID, email, JWT, or tenant/org/workspace ID. Adapters (the code that talks HTTP to Conduit) are responsible for stripping cookies and tokens out of observations before they hit the store; the schema is the contract that says "no, you may not put that here".

## A worked example

The Phase 2 Conduit goal slate has five entries, parallel-but-distinct to the Phase 1 four: `login`, `publish_article`, `favorite_article`, `follow_user`, and `edit_article`. Each has its own seeded knowledge file under `experiments/regression_recall_real/knowledge/`. Each one also declares `auth_state: {authenticated: true, scope: user}`, because every Phase 2 goal probes a logged-in surface.

Take the Phase 2 `login` goal on Conduit. Its seeded knowledge file declares two network success signals (`POST /api/users/login returns 200 with a token`, and `GET /api/user with that token returns 200`), one risk (`stale-bearer-token`: login can return 200 with a token the server never actually registered), and an `auth_state` projection of `{authenticated: true, scope: user}`.

A planted regression makes `POST /api/users/login` return 200 with a token, but `GET /api/user` with that same token returns 401. An R-mode (regression-recall) arm that trusts only the first success signal passes blindly and misses the bug. An arm that reads the risk's trigger, plus the `auth_state` projection saying "I should be at user scope after this", knows to probe `GET /api/user` as an independent check and catches the regression. The point of `auth_state` is that the next goal (publish, favorite, follow, edit) inherits the same posture instead of re-deriving "am I authenticated?" from raw signals each time.

## What it does NOT do

- It does not store credentials of any kind. `auth_state.scope` of `"Bearer eyJ..."`, `"user_id=42"`, or `"jamie@example.com"` is rejected loudly at write time. That is the entire point.
- It does not test Praxis on multiple apps. One real app (Conduit) is the Phase 2 deliverable; cross-app sweep is Phase 3.
- It does not change the Phase 1 testapp experiment. The old `experiments/regression_recall/` directory is sealed and untouched; the new work lives in `experiments/regression_recall_real/`.
- It does not handle concurrency, tenant isolation, or candidate auth scopes. Those are owned by other ADRs (0012, 0013, 0014, 0015).
- It does not promise the bring-up is fast on every laptop. The contract is "under 30 minutes wall time on commodity hardware including image pull"; cold-cache typical runs are well under 10 minutes.

## How to verify it works for you

The fast, default-on tests cover schema validation, seed shape, and adapter redaction:

```bash
python -m pytest tests/test_regression_recall_real.py -q
```

The slow gate (actually bring Conduit up and probe the API) is opt-in so `bash verify.sh` stays fast:

```bash
PRAXIS_RUN_CONDUIT_BRINGUP=1 python -m pytest tests/test_conduit_bringup.py -q
```

Both passing means: Conduit boots within the 30-minute ceiling, every seeded goal carries a valid `auth_state`, and no seed file contains any of the forbidden credential-shaped substrings.

## Reference

- `docs/adr/0016-real-app-sut-selection.md` for the sealed selection criteria, the Conduit recommendation, and the Saleor fallback path.
- `docs/adr/0017-schema-extension-auth-state.md` for the `auth_state` field shape, the rejection rules, and the same-commit schema-plus-model-plus-agreement-test discipline.
