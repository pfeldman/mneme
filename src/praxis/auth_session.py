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
from typing import Any

__all__ = [
    "AUTH_DIRNAME",
    "ENV_PREFIX",
    "MissingSession",
    "env_var_name",
    "session_file_path",
    "load_session",
    "save_session",
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
    """

    def __init__(self, role: str) -> None:
        self.role = role
        super().__init__(role)

    def __str__(self) -> str:  # plain, no KeyError quoting of the role
        return (
            f"missing auth session for role {self.role!r}: set the environment "
            f"variable {env_var_name(self.role)} to the storageState JSON, or "
            f"save one to {AUTH_DIRNAME}/{self.role}.json "
            f"(seed it with a teach login)."
        )


def env_var_name(role: str) -> str:
    """Return the env / CI runner-secret name for a role: `PRAXIS_AUTH_STATE_<ROLE>`.

    The role is uppercased (ADR-0026 decision 3). This is a pure name mapping;
    it never reads or reveals a session value.
    """
    return f"{ENV_PREFIX}{role.upper()}"


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
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
) -> Path | None:
    """Resolve the local session file path for a role: `.praxis.auth/<role>.json`.

    `auth_dir` (or `repo_root`) pins the directory for tests and for callers
    that already resolved the project; absent both, the repo root is found by
    walking up from cwd to the directory holding `.praxis/`. Returns None when
    no `.praxis/` is found and neither override is given (there is no repo root
    to anchor the sibling directory). The path is ALWAYS outside the committed
    `.praxis/` tree: `.praxis.auth/` is a sibling, never a child.
    """
    if auth_dir is not None:
        return auth_dir / f"{role}.json"
    root = repo_root if repo_root is not None else _find_repo_root()
    if root is None:
        return None
    return root / AUTH_DIRNAME / f"{role}.json"


def load_session(
    role: str,
    *,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the parsed storageState for `role`, environment winning over the file.

    Resolution order (ADR-0026 decision 3, mirroring `secrets.get_credential`):

        1. the environment variable `PRAXIS_AUTH_STATE_<ROLE>` (a CI runner
           secret) whose content is the RAW storageState JSON, then
        2. the gitignored `.praxis.auth/<role>.json` local file.

    The environment always wins, so CI can supply the session with no file
    present and local use supplies it through the file. `auth_dir` (or
    `repo_root`) pins the file location for tests and for callers that already
    resolved the project; absent both, the file is found by walking up from cwd
    to the directory holding `.praxis/`.

    Returns the parsed storageState as a dict. Raises `MissingSession(role)`
    when neither source provides a session. The raised value names only the
    absent ROLE, never any session content.
    """
    env = os.environ if environ is None else environ
    var = env_var_name(role)
    raw = env.get(var)
    if raw is not None and raw != "":
        return _parse_session(raw, role)

    path = session_file_path(role, repo_root=repo_root, auth_dir=auth_dir)
    if path is not None and path.is_file():
        return _parse_session(path.read_text(encoding="utf-8"), role)

    raise MissingSession(role)


def save_session(
    role: str,
    session: dict[str, Any],
    *,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
) -> Path:
    """Save a storageState for `role` to the local `.praxis.auth/<role>.json` file.

    Used by the teach bootstrap (ADR-0026 decision 7): after a confirmed login
    that passed 2FA live, the resulting authenticated session is saved to the
    secret channel for later reuse. Creates `.praxis.auth/` if needed. The
    directory is a sibling of `.praxis/`, never inside the committed tree, and is
    gitignored (Step 2) before any session could be written.

    `auth_dir` (or `repo_root`) pins the directory for tests; absent both, the
    repo root is found by walking up from cwd. Returns the written path. The
    session content is written to the gitignored file only; this function prints
    and logs nothing, so no cookie or token is ever echoed.
    """
    path = session_file_path(role, repo_root=repo_root, auth_dir=auth_dir)
    if path is None:
        raise SystemExit(
            f"no praxis project found to anchor {AUTH_DIRNAME}/ for role "
            f"{role!r}. Run `praxis init` in your project root first."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # `json.dumps` of the dict, never a print: the content stays on disk only.
    path.write_text(json.dumps(session), encoding="utf-8")
    return path


def _parse_session(raw: str, role: str) -> dict[str, Any]:
    """Parse raw storageState JSON into a dict, naming the role on a bad payload.

    A malformed session is a loud failure that names the ROLE, never the raw
    content: the error message must not echo the (possibly secret-bearing)
    payload. Raises `MissingSession(role)` so a caller that already handles the
    absent case also handles an unusable one, both keyed by role and never by
    value.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Do NOT include `raw` (it carries cookies / tokens): name only the role.
        raise MissingSession(role) from None
    if not isinstance(parsed, dict):
        raise MissingSession(role)
    return parsed
