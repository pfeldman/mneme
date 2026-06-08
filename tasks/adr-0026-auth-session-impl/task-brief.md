# Task brief (synthesized from the session, 2026-06-08)

Implement ADR-0026 (persistent authenticated-session reuse), the decision we
sealed and merged this session. Goal: let Praxis test a real app whose login
needs 2FA (the motivating case is Digioh prod with email-delivered 2FA via
Gmail) without passing 2FA on every run, by saving and reusing the Playwright
authenticated session.

What to build (per ADR-0026):
- a session store: save/load the Playwright storageState as a SECRET, keyed per
  role, on the ADR-0021 channel (gitignored local file, env / CI runner secret
  wins over the file), never committed, never in knowledge (only the ADR-0017
  abstract auth_state is recorded);
- `praxis init` gitignores the session path before any session is written;
- AUTH-EXPIRED as a THIRD regress outcome distinct from OK / REGRESSED / STALE:
  a run that finds the saved session expired (a login wall when authenticated
  was expected) is named loudly, never a false REGRESSED and never silent green;
- the teach skill saves the session after a human login (the 2FA bootstrap);
  the regress / explore skills load it before driving the browser, and on
  AUTH-EXPIRED ask the human to re-auth (skill) or fail loud (console / CI).

Division of labor: the library provides the session STORE and the AUTH-EXPIRED
verdict (fully testable, no browser); the actual browser-side storageState
export/import is done by the skill through the Playwright MCP (the live brain),
proven in a separate live run like the teach proof.

Conventions: branch off main, ASCII only, bash verify.sh ALL GREEN per commit,
one commit per step, no Co-Authored-By. Do NOT activate the deferred schema
fields states/paths or a refuted status.
