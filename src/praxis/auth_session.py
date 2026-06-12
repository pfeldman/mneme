"""Authenticated-session channel for live runs (ADR-0026 decisions 2 and 3).

A saved session is the Playwright "storage state": the cookies and local
storage of a logged-in browser, the artifact that proves a logged-in session.
Reusing it across teach / regress / explore runs lets a goal run authenticated
WITHOUT a fresh login, hence without a fresh 2FA (ADR-0026 decision 1).

The saved session contains live session cookies and tokens, so it IS a secret
of the same class as a password (ADR-0026 decision 2). It is treated exactly
like the ADR-0021 `.praxis.secrets` channel and mirrors `praxis.secrets`:

    - environment variables (a CI runner secret in automation) carry the RAW
      storageState JSON, and / or
    - a gitignored `.praxis.auth/<role>.json` file at the repo root, a sibling
      of `.praxis.secrets` and `.praxis/`, never inside the committed tree.

An environment variable WINS over the file (ADR-0026 decision 3): CI supplies
the session as a runner secret with no file, and local use supplies it through
the file. The env var named for a role is `PRAXIS_AUTH_STATE_<ROLE>` (the role
uppercased), and its content is the raw storageState JSON (ADR-0021 decision 6,
ADR-0026 decision 3).

The session is keyed by the ABSTRACT role it authenticates as (the ADR-0017
`auth_state.scope`: `anonymous`, `user`, `admin`, or a SUT-specific role), not
by an individual goal (ADR-0026 decision 4). All goals targeting the same role
reuse the same saved session.

Multi-environment projects (ADR-0035 decision 7) scope the channel per
deployment: with an environment selected, the resolution is the env var
`PRAXIS_AUTH_STATE_<ENV>_<ROLE>` (env and role uppercased), then the file
`.praxis.auth/<env>/<role>.json` - and NOTHING else. There is deliberately NO
fallback to the unscoped `PRAXIS_AUTH_STATE_<ROLE>` / `.praxis.auth/<role>.json`
sources: a storage state is domain-bound (a prod cookie does nothing on dev2's
host and vice versa), so silently reusing an unscoped session against a selected
environment would yield a confusing AUTH-EXPIRED at best and a run against the
wrong logged-in surface at worst. A missing env-scoped session is a loud
`MissingSession` naming the role AND the environment, so the operator knows
exactly which session to seed. With no environment selected (`environment=None`,
the undeclared project), resolution is byte-identical to the unscoped channel
above. An empty-string environment counts as unset (the ADR-0034 posture). The
environment name is part of the env-var name and the file PATH only; it is never
written into the session content.

The session is NEVER written into any committed file under `.praxis/` (no
knowledge file, no candidate file, no committed run artifact). Knowledge records
only the ADR-0017 abstract `auth_state`; the session, its cookies, and its
tokens never cross into an emitted assertion. The local file lives at
`.praxis.auth/<role>.json`, a sibling of the committed tree, gitignored
separately (Step 2).

A session VALUE (its cookies / tokens) is never echoed back into stdout, a log,
an exception message, or the chat by this module. Only role NAMES and the
resolved file path ever leave it. `MissingSession` and the helpers below name
the absent ROLE, never the session content.

This module imports only the standard library, so importing it never pulls a
runtime or a brain (ADR-0003, ADR-0019). It also performs no I/O at import time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import AuthState

__all__ = [
    "AUTH_DIRNAME",
    "ENV_PREFIX",
    "MissingSession",
    "env_var_name",
    "session_file_path",
    "load_session",
    "save_session",
    "role_for_auth_state",
    "role_for_session_reuse",
    "save_session_for_role",
    "load_session_for_role",
]

# The local session directory name. It is a sibling of `.praxis/` and
# `.praxis.secrets` at the repo root, and is gitignored by `praxis init`
# (ADR-0026 decision 3, gitignored in Step 2) BEFORE any session could be
# written, so it can never be committed by accident. One file per role lives
# inside it as `<role>.json`.
AUTH_DIRNAME = ".praxis.auth"

# The environment-variable / CI runner-secret name prefix. The full name for a
# role is `PRAXIS_AUTH_STATE_<ROLE>` with the role uppercased (ADR-0026
# decision 3). Its content is the RAW storageState JSON.
ENV_PREFIX = "PRAXIS_AUTH_STATE_"


class MissingSession(KeyError):
    """No saved session exists for a role in the environment or the file.

    Carries the offending ROLE name (never the session content). Subclasses
    `KeyError` so callers that already guard `KeyError` keep working, but the
    string form names the role plainly rather than the `KeyError` repr. It
    mirrors `praxis.secrets.MissingCredential`.

    `environment` (ADR-0035 decision 7) names the selected environment whose
    env-scoped sources were consulted, or None when no environment was selected.
    With `environment=None` the string form is byte-identical to the
    pre-ADR-0035 message; with an environment it names the role AND the
    environment plus the env-scoped env-var name and file path, and states the
    deliberate no-fallback rule so the operator knows the unscoped session was
    NOT silently borrowed.
    """

    def __init__(self, role: str, environment: str | None = None) -> None:
        self.role = role
        self.environment = environment if environment else None
        super().__init__(role)

    def __str__(self) -> str:  # plain, no KeyError quoting of the role
        if self.environment is None:
            return (
                f"missing auth session for role {self.role!r}: set the environment "
                f"variable {env_var_name(self.role)} to the storageState JSON, or "
                f"save one to {AUTH_DIRNAME}/{self.role}.json "
                f"(seed it with a teach login)."
            )
        return (
            f"missing auth session for role {self.role!r} on environment "
            f"{self.environment!r}: set the environment variable "
            f"{env_var_name(self.role, self.environment)} to the storageState "
            f"JSON, or save one to "
            f"{AUTH_DIRNAME}/{self.environment}/{self.role}.json "
            f"(seed it with a teach login against that environment). Sessions "
            f"are domain-bound, so the unscoped "
            f"{AUTH_DIRNAME}/{self.role}.json is deliberately NOT used "
            f"(ADR-0035 decision 7)."
        )


def env_var_name(role: str, environment: str | None = None) -> str:
    """Return the env / CI runner-secret name for a role.

    Unscoped (`environment=None`): `PRAXIS_AUTH_STATE_<ROLE>`. Env-scoped
    (ADR-0035 decision 7): `PRAXIS_AUTH_STATE_<ENV>_<ROLE>`, the environment and
    the role uppercased with the same rule (the name is constructed, never
    parsed, so the `_` join is unambiguous). An empty-string environment counts
    as unset. This is a pure name mapping; it never reads or reveals a session
    value.
    """
    if not environment:
        return f"{ENV_PREFIX}{role.upper()}"
    return f"{ENV_PREFIX}{environment.upper()}_{role.upper()}"


def _find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or cwd) to the directory holding `.praxis/`.

    Returns that directory (the repo root, where `.praxis.auth/` lives as a
    sibling of `.praxis/`), or None when no `.praxis/` is found upward. This
    mirrors the discovery in `praxis.secrets` and `praxis.cli.main`.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".praxis").is_dir():
            return candidate
    return None


def session_file_path(
    role: str,
    *,
    environment: str | None = None,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
) -> Path | None:
    """Resolve the local session file path for a role.

    Unscoped (`environment=None`): `.praxis.auth/<role>.json`. Env-scoped
    (ADR-0035 decision 7): `.praxis.auth/<env>/<role>.json`, a per-env subdir
    under the same gitignored directory; the environment name is part of the
    PATH only, never of the session content. An empty-string environment counts
    as unset.

    `auth_dir` (or `repo_root`) pins the directory for tests and for callers
    that already resolved the project; absent both, the repo root is found by
    walking up from cwd to the directory holding `.praxis/`. Returns None when
    no `.praxis/` is found and neither override is given (there is no repo root
    to anchor the sibling directory). The path is ALWAYS outside the committed
    `.praxis/` tree: `.praxis.auth/` is a sibling, never a child.
    """
    rel = f"{environment}/{role}.json" if environment else f"{role}.json"
    if auth_dir is not None:
        return auth_dir / rel
    root = repo_root if repo_root is not None else _find_repo_root()
    if root is None:
        return None
    return root / AUTH_DIRNAME / rel


def load_session(
    role: str,
    *,
    environment: str | None = None,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the parsed storageState for `role`, environment winning over the file.

    Resolution order (ADR-0026 decision 3, mirroring `secrets.get_credential`):

        1. the environment variable `PRAXIS_AUTH_STATE_<ROLE>` (a CI runner
           secret) whose content is the RAW storageState JSON, then
        2. the gitignored `.praxis.auth/<role>.json` local file.

    With an `environment` selected (ADR-0035 decision 7) BOTH sources are
    env-scoped and nothing else is consulted: the env var
    `PRAXIS_AUTH_STATE_<ENV>_<ROLE>`, then the file
    `.praxis.auth/<env>/<role>.json`. There is deliberately NO fallback to the
    unscoped sources: a storage state is domain-bound, so an unscoped session
    present while an environment is selected is still a loud
    `MissingSession(role, environment)`, never a silent wrong-domain reuse.
    With `environment=None` resolution is byte-identical to the unscoped
    channel; an empty-string environment counts as unset.

    The environment variable always wins, so CI can supply the session with no
    file present and local use supplies it through the file. `auth_dir` (or
    `repo_root`) pins the file location for tests and for callers that already
    resolved the project; absent both, the file is found by walking up from cwd
    to the directory holding `.praxis/`.

    Returns the parsed storageState as a dict. Raises
    `MissingSession(role, environment)` when neither source provides a session.
    The raised value names only the absent ROLE (and the selected environment),
    never any session content.
    """
    environment = environment if environment else None
    env = os.environ if environ is None else environ
    var = env_var_name(role, environment)
    raw = env.get(var)
    if raw is not None and raw != "":
        return _parse_session(raw, role, environment)

    path = session_file_path(
        role, environment=environment, repo_root=repo_root, auth_dir=auth_dir,
    )
    if path is not None and path.is_file():
        return _parse_session(path.read_text(encoding="utf-8"), role, environment)

    raise MissingSession(role, environment)


def save_session(
    role: str,
    session: dict[str, Any],
    *,
    environment: str | None = None,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
) -> Path:
    """Save a storageState for `role` to the local `.praxis.auth/` channel.

    Used by the teach bootstrap (ADR-0026 decision 7): after a confirmed login
    that passed 2FA live, the resulting authenticated session is saved to the
    secret channel for later reuse. Creates `.praxis.auth/` if needed. The
    directory is a sibling of `.praxis/`, never inside the committed tree, and is
    gitignored (Step 2) before any session could be written.

    With an `environment` selected (ADR-0035 decision 7) the session lands in
    the per-env subdir `.praxis.auth/<env>/<role>.json` (a teach login on a
    selected environment seeds THAT deployment's session); with
    `environment=None` it is today's `.praxis.auth/<role>.json`. The environment
    name is part of the PATH only, never written into the session content. An
    empty-string environment counts as unset.

    `auth_dir` (or `repo_root`) pins the directory for tests; absent both, the
    repo root is found by walking up from cwd. Returns the written path. The
    session content is written to the gitignored file only; this function prints
    and logs nothing, so no cookie or token is ever echoed.
    """
    environment = environment if environment else None
    path = session_file_path(
        role, environment=environment, repo_root=repo_root, auth_dir=auth_dir,
    )
    if path is None:
        raise SystemExit(
            f"no praxis project found to anchor {AUTH_DIRNAME}/ for role "
            f"{role!r}. Run `praxis init` in your project root first."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # `json.dumps` of the dict, never a print: the content stays on disk only.
    path.write_text(json.dumps(session), encoding="utf-8")
    return path


def _parse_session(
    raw: str, role: str, environment: str | None = None,
) -> dict[str, Any]:
    """Parse raw storageState JSON into a dict, naming the role on a bad payload.

    A malformed session is a loud failure that names the ROLE (and the selected
    environment, when there is one), never the raw content: the error message
    must not echo the (possibly secret-bearing) payload. Raises
    `MissingSession(role, environment)` so a caller that already handles the
    absent case also handles an unusable one, both keyed by role and never by
    value.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Do NOT include `raw` (it carries cookies / tokens): name only the role.
        raise MissingSession(role, environment) from None
    if not isinstance(parsed, dict):
        raise MissingSession(role, environment)
    return parsed


# --- the skill seams (ADR-0026 decisions 1, 7) ----------------------------
#
# The skills (`/praxis:teach`, `/praxis:regress`, `/praxis:explore`) drive the
# live browser through the brain + Playwright MCP; the library owns the session
# STORE. These three thin seams are the entry points the skills call so the role
# resolution (ADR-0026 decision 4: one session per ABSTRACT role, keyed by the
# ADR-0017 `auth_state.scope`) lives in ONE place and reuses `save_session` /
# `load_session` above without duplicating the env-wins-over-file resolution.
#
# What crosses each seam: only the session SECRET (the storageState dict) and
# the ABSTRACT role name. A credential and the 2FA code NEVER cross any of these
# seams (ADR-0022 decision 5); the skill passes 2FA live in the browser and the
# resulting authenticated session is what `save_session_for_role` persists.


def role_for_auth_state(auth_state: "AuthState | None") -> str | None:
    """The abstract role a goal's session is keyed by, or None.

    A session is keyed by the ADR-0017 `auth_state.scope` (ADR-0026 decision 4),
    not by an individual goal: all goals targeting the same role reuse the same
    saved session. Returns the scope string when the goal expects an
    authenticated, non-anonymous role; returns None when there is no auth_state,
    the goal is anonymous-scoped, or the auth_state does not claim an
    authenticated session (those goals need no saved session). This is the same
    predicate `runner.regression._expected_authenticated_scope` applies, kept in
    sync so the role a session is SAVED under is the role the regress classifier
    EXPECTS.
    """
    if auth_state is None or not auth_state.authenticated:
        return None
    scope = auth_state.scope
    if scope is None or scope.strip().lower() == "anonymous":
        return None
    return scope


def role_for_session_reuse(auth_state: "AuthState | None") -> str | None:
    """The role a run may REUSE a saved session for, or None to force a login.

    Session reuse is for goals where authentication is a PRECONDITION: the login
    is setup, not the test, so reusing the saved session to skip the per-run 2FA
    is correct (ADR-0026). It is FORBIDDEN for a goal where authentication is the
    SUBJECT under test (`auth_state.being_tested`, ADR-0027 decision 2): reusing
    a session would skip the very login flow the goal verifies and make it pass
    without exercising it.

    Returns the reuse role (the `role_for_auth_state` scope) only when the goal
    is a precondition goal; returns None when `being_tested` is true (the run
    must perform a real login and must NOT load a saved session) or when there is
    no authenticated role to key a session by. This is deliberately distinct from
    `role_for_auth_state`, which stays the role a session is SAVED under and the
    role the regress classifier EXPECTS: an auth-subject goal still authenticates
    as that scope, it just may not short-circuit the login by reusing a session.
    """
    if auth_state is not None and auth_state.being_tested:
        return None
    return role_for_auth_state(auth_state)


def save_session_for_role(
    role: str,
    session: dict[str, Any],
    *,
    environment: str | None = None,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
) -> Path:
    """SAVE seam: persist a confirmed-login storageState for a role (the teach
    bootstrap, ADR-0026 decision 7).

    The teach flow calls this AFTER a human login that passed 2FA live (the code
    drove the browser and was never persisted, ADR-0022 decision 5): the
    resulting authenticated browser session is exported as a storageState dict
    and saved to the secret channel for later reuse. It is a thin alias over
    `save_session` so the resolution logic is not duplicated; the role is the
    abstract ADR-0017 scope (`role_for_auth_state`), and `environment` is the
    run's selected environment (ADR-0035 decision 7: a teach login on a selected
    environment saves to `.praxis.auth/<env>/<role>.json`; None saves unscoped
    exactly as before). Returns the written path. The session is written to the
    gitignored file only; no cookie or token is echoed.
    """
    return save_session(
        role, session, environment=environment,
        repo_root=repo_root, auth_dir=auth_dir,
    )


def load_session_for_role(
    role: str,
    *,
    environment: str | None = None,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """LOAD seam: fetch the saved storageState for a role before driving the
    browser (regress / explore, ADR-0026 decisions 1, 3).

    The regress / explore flow calls this before the brain drives the live app:
    the returned storageState is injected into the browser context so a goal
    runs authenticated WITHOUT a fresh login, hence without a fresh 2FA. It is a
    thin alias over `load_session`, so the env-wins-over-file resolution
    (ADR-0026 decision 3: a CI runner secret beats the local file) and the
    ADR-0035 env scoping (with an `environment` selected, ONLY the env-scoped
    sources resolve; no cross-env or unscoped fallback) are shared, not
    re-implemented. Raises `MissingSession(role, environment)` (naming only the
    role and the selected environment, never the session value) when no session
    exists; the skill then asks the human to re-authenticate (the AUTH-EXPIRED
    re-seed), the console / CI surface fails loudly.
    """
    return load_session(
        role, environment=environment,
        repo_root=repo_root, auth_dir=auth_dir, environ=environ,
    )
