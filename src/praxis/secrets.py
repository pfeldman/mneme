"""Credentials channel for live runs (ADR-0021 decision 6).

The credentials a teach / regress / explore run needs to authenticate against
the live app are NOT knowledge. They never enter `.praxis/`, never get written
into any committed file, candidate, knowledge file, or log, and never appear in
git history. They live in a separate, gitignored channel:

    - environment variables (a CI runner secret in automation), and / or
    - a gitignored `.praxis.secrets` file at the repo root, a sibling of
      `.praxis/`, never inside the committed tree, in `KEY=value` form.

An environment variable takes precedence over the file, so CI supplies
credentials as runner secrets with no file and local use supplies them through
the file (ADR-0021 decision 6).

The behavior on a missing credential splits by surface (ADR-0019):

    - the console / CI surface fails LOUDLY: it raises / exits non-zero, names
      the absent key and how to set it (an environment variable or the secrets
      file), and never prompts, because there is no human to answer
      (`require_credential`, `fail_loud_missing`);
    - the Claude Code skill surface asks the user for the missing key and offers
      the exact append command to add it, for example
      `! echo "APP_USERNAME=<value>" >> .praxis.secrets` (`ask_prompt`).

A secret VALUE is never echoed back into stdout, a log, an exception message, or
the chat by this module. Only key NAMES and the literal placeholder
`<value>` ever leave this module. `MissingCredential` and the helpers below name
the absent key, never a present one's value.

This module imports only the standard library, so importing it never pulls a
runtime or a brain (ADR-0003, ADR-0019). It also performs no I/O at import time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "SECRETS_FILENAME",
    "MissingCredential",
    "load_secrets_file",
    "get_credential",
    "require_credential",
    "fail_loud_missing",
    "ask_prompt",
    "append_command",
]

# The credentials file name. It is a sibling of `.praxis/` at the repo root and
# is gitignored by `praxis init` (ADR-0021 decisions 5 and 6) BEFORE any secret
# could be written, so it can never be committed by accident.
SECRETS_FILENAME = ".praxis.secrets"

# The literal placeholder offered in append commands and prompts. A real value
# is NEVER substituted into this string by this module; the user fills it in.
_VALUE_PLACEHOLDER = "<value>"


class MissingCredential(KeyError):
    """A required credential is absent from both the environment and the file.

    Carries the offending key NAME (never a value). Subclasses `KeyError` so
    callers that already guard `KeyError` keep working, but the string form
    names the key plainly rather than the `KeyError` repr.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)

    def __str__(self) -> str:  # plain, no KeyError quoting of the key
        return (
            f"missing credential {self.key!r}: set it as an environment "
            f"variable or add it to {SECRETS_FILENAME} "
            f"(KEY=value, one per line)."
        )


def _find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or cwd) to the directory holding `.praxis/`.

    Returns that directory (the repo root, where `.praxis.secrets` lives as a
    sibling of `.praxis/`), or None when no `.praxis/` is found upward.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".praxis").is_dir():
            return candidate
    return None


def load_secrets_file(path: Path) -> dict[str, str]:
    """Parse a `.praxis.secrets` `KEY=value` file into a dict.

    Splits each non-blank, non-comment line on the FIRST `=` so a value may
    itself contain `=` (a base64 token, a connection string). Surrounding
    whitespace around the key is stripped; the value is taken verbatim after the
    first `=` (leading / trailing whitespace on the value is stripped so a
    trailing newline does not become part of the secret). A missing file is an
    empty dict, never an error: the environment may still supply every key.

    This function reads values into memory but NEVER prints, logs, or returns
    them in any form a caller would surface to stdout; only `get_credential`
    consumes the dict, and it returns a single requested value to its caller.
    """
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            # A malformed line carries no key=value; skip it rather than guess.
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = value.strip()
    return out


def get_credential(
    key: str,
    *,
    repo_root: Path | None = None,
    secrets_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Return the credential for `key`, environment winning over the file.

    Resolution order (ADR-0021 decision 6):

        1. an environment variable named `key` (a CI runner secret), then
        2. the `KEY=value` line in `.praxis.secrets`.

    The environment always wins, so CI can supply a secret with no file present
    and local use supplies it through the file. `secrets_path` (or `repo_root`)
    pins the file location for tests and for callers that already resolved the
    project; absent both, the file is found by walking up from cwd to the
    directory holding `.praxis/`.

    Raises `MissingCredential(key)` when neither source provides the key. The
    raised value names only the absent KEY, never any present value.
    """
    env = os.environ if environ is None else environ
    if key in env and env[key] != "":
        return env[key]

    path = secrets_path
    if path is None:
        root = repo_root if repo_root is not None else _find_repo_root()
        if root is not None:
            path = root / SECRETS_FILENAME
    if path is not None:
        file_secrets = load_secrets_file(path)
        if key in file_secrets:
            return file_secrets[key]

    raise MissingCredential(key)


def append_command(key: str) -> str:
    """Return the exact shell command the skill surface offers for a missing key.

    The command appends a placeholder line to `.praxis.secrets`; the user
    replaces the placeholder with the real value. This module never substitutes
    a real value into the command (ADR-0021 decision 6: a secret value is never
    echoed by this module).
    """
    return f'! echo "{key}={_VALUE_PLACEHOLDER}" >> {SECRETS_FILENAME}'


def ask_prompt(key: str) -> str:
    """Return the user-facing ask the Claude Code skill surface shows (ADR-0019).

    The skill is human-in-the-loop, so the missing-credential behavior is to ask
    the user for the key and offer the exact append command rather than fail.
    This function returns the text to show; it performs no I/O and reveals no
    value, only the key name and the placeholder append command.
    """
    return (
        f"I need the credential {key!r} to authenticate against the app, but it "
        f"is not set. Add it to {SECRETS_FILENAME} (gitignored, never committed) "
        f"with this command, replacing the placeholder with your value:\n"
        f"  {append_command(key)}\n"
        f"Or export it as the environment variable {key}."
    )


def fail_loud_missing(exc: MissingCredential, *, stream: object | None = None) -> None:
    """Write the loud, no-prompt missing-credential message for the console / CI.

    ADR-0019 / ADR-0021 decision 6: on the console surface (and in CI) there is
    no human to answer, so the operation must fail LOUDLY. This writes a message
    that NAMES the absent key and how to set it (environment variable or the
    secrets file) to `stream` (default stderr). It echoes no value: only the key
    name and the placeholder append command appear. The caller exits non-zero
    (see `require_credential`); this helper only formats the message.
    """
    out = sys.stderr if stream is None else stream
    print(
        f"ERROR: missing credential {exc.key!r}.\n"
        f"  set it as an environment variable:  export {exc.key}=...\n"
        f"  or add it to {SECRETS_FILENAME}:        "
        f'echo "{exc.key}={_VALUE_PLACEHOLDER}" >> {SECRETS_FILENAME}\n'
        f"  ({SECRETS_FILENAME} is gitignored; the value is never committed or "
        f"logged.)",
        file=out,  # type: ignore[arg-type]
    )


def require_credential(
    key: str,
    *,
    repo_root: Path | None = None,
    secrets_path: Path | None = None,
    environ: dict[str, str] | None = None,
    stream: object | None = None,
) -> str:
    """Console / CI accessor: return the credential or fail LOUDLY, non-zero.

    Wraps `get_credential` for the deterministic console surface (ADR-0019).
    On success it returns the value. On a missing key it writes the loud,
    key-naming, no-prompt message (`fail_loud_missing`) and raises `SystemExit`
    with a non-zero code, so a CI run exits red with the key named and no
    interactive prompt. The secret value is never printed on either path.
    """
    try:
        return get_credential(
            key,
            repo_root=repo_root,
            secrets_path=secrets_path,
            environ=environ,
        )
    except MissingCredential as exc:
        fail_loud_missing(exc, stream=stream)
        raise SystemExit(2) from exc
