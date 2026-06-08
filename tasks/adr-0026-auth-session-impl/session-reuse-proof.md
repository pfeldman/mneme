# ADR-0026 live proof: session reuse + AUTH-EXPIRED on testapp

The pre-merge validation Pablo asked for: prove the session-reuse mechanism
and the AUTH-EXPIRED verdict end to end with a REAL browser and the REAL
modules, before merging the implementation. Run 2026-06-08.

## Setup

- SUT: `experiments/ui-mutation/testapp.py` on `http://127.0.0.1:8000`. Its
  `/me` endpoint is gated on the session cookie: it returns 200 when the
  cookie matches a valid server-side session, 401 otherwise. This is the
  cookie-gated authenticated area a real app has and the toy needed for the
  proof.
- Browser: a live Playwright MCP session (the local-brain path).
- Modules exercised, unmodified: `praxis.auth_session` (the session store) and
  `praxis.runner.regression.classify_goal` (the AUTH-EXPIRED classifier).
- testapp has no 2FA, but the session-reuse mechanism is identical: reusing a
  saved session skips the entire login, which on a real app is exactly what
  skips the 2FA step. The 2FA-specific run is the separate Digioh pass.

## What was proven

1. **Login, then authenticated.** Logged in through the real form; `GET /me`
   returned 200 `{"authenticated": true, "session_id": "ok"}`, cookie
   `session=ok`.

2. **The real store saved and reloaded the live session.** In a fresh
   `praxis init` project, `auth_session.save_session_for_role("user", ...)`
   wrote the captured browser session to `.praxis.auth/user.json` and
   `load_session_for_role("user")` reloaded it byte-for-byte. Confirmed:
   `.praxis.auth/` was gitignored BEFORE the write (init wrote the ignore
   line), the file is outside the committed `.praxis/` tree, and `git status`
   never shows it (the secret can never be committed).

3. **Reuse skips the login.** In the live browser, clearing the cookie (a
   fresh browser / new machine) made `GET /me` return 401 (logged out).
   Injecting the saved session (`session=ok`) made `GET /me` return 200 again,
   authenticated WITHOUT a second login. The saved session re-authenticates a
   fresh browser, which is the mechanism that removes the per-run 2FA cost on a
   real app.

4. **AUTH-EXPIRED, not a false REGRESSED.** Invalidating the session
   server-side (the cookie stays, the server forgets it) made `GET /me` return
   401: an expired session. Feeding the observed `authenticated=False` to the
   real `classify_goal` against a goal whose `auth_state.scope` is `user`
   produced verdict `AUTH-EXPIRED` (not REGRESSED, not STALE), naming the role
   `user`, failing the run loudly. The routing evidence said the run could not
   authenticate as role user, the saved session is expired, refresh it, and
   this is neither a regression (the app did not break) nor stale knowledge. A
   real regression (an authenticated run whose failure signal fired) still
   classifies REGRESSED; the auth route only fires on `authenticated=False`.

## Verdict

The ADR-0026 implementation works end to end on a real browser with the real
modules: the session is saved as a gitignored secret, reused to skip the login,
and a stale session surfaces as a distinct, loud AUTH-EXPIRED rather than a
false regression. This is the pre-merge proof. The next pass is the real-world
run against Digioh (with a low-privilege test account and its email 2FA), which
is where the 2FA-skip payoff is exercised against an app we did not build.
