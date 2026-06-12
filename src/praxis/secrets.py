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

Multi-environment projects (ADR-0035 decision 7) add ONE optional level: with
an environment selected, a gitignored per-env OVERLAY file
`.praxis.secrets.<env>` (a sibling of the base file, same format, same walk-up)
wins over the base `.praxis.secrets` for the keys it defines. A key absent from
the overlay falls through to the base, so shared keys live once in the base
file. The `KEY` environment variable still beats both. With no environment
selected (`environment=None`), the overlay is never consulted and resolution is
exactly the two-level ADR-0021 channel above.

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
    "overlay_filename",
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


def overlay_filename(environment: str) -> str:
    """Return the per-environment overlay file name (ADR-0035 decision 7).

    The overlay is a sibling of the base `.praxis.secrets`, named
    `.praxis.secrets.<env>`, gitignored by the `praxis init` `.praxis.secrets.*`
    ignore line so it can never be committed by accident.
    """
    return f"{SECRETS_FILENAME}.{environment}"

# The literal placeholder offered in append commands and prompts. A real value
# is NEVER substituted into this string by this module; the user fills it in.
_VALUE_PLACEHOLDER = "<value>"


class MissingCredential(KeyError):
    """A required credential is absent from both the environment and the file.

    Carries the offending key NAME (never a value). Subclasses `KeyError` so
    callers that already guard `KeyError` keep working, but the string form
    names the key plainly rather than the `KeyError` repr.

    `environment` (ADR-0035 decision 7) names the selected environment whose
    overlay file was also consulted, or None when no environment was selected.
    With `environment=None` the string form is byte-identical to the pre-ADR-0035
    message; with an environment it additionally names the consulted overlay
    file in the searched-locations part.
    """

    def __init__(self, key: str, environment: str | None = None) -> None:
        self.key = key
        self.environment = environment
        super().__init__(key)

    def __str__(self) -> str:  # plain, no KeyError quoting of the key
        searched = SECRETS_FILENAME
        if self.environment is not None:
            searched = f"{overlay_filename(self.environment)} or {SECRETS_FILENAME}"
        return (
            f"missing credential {self.key!r}: set it as an environment "
            f"variable or add it to {searched} "
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
    environment: str | None = None,
    repo_root: Path | None = None,
    secrets_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Return the credential for `key`, environment winning over the files.

    Resolution order (ADR-0021 decision 6, extended by ADR-0035 decision 7):

        1. an environment variable named `key` (a CI runner secret), then
        2. with an `environment` selected, the `KEY=value` line in the per-env
           OVERLAY `.praxis.secrets.<env>` (a sibling of the base file), then
        3. the `KEY=value` line in the base `.praxis.secrets`.

    The environment variable always wins, so CI can supply a secret with no
    file present and local use supplies it through the files. The overlay is an
    OVERLAY, not a replacement: a key it defines shadows the base, a key it
    omits falls through to the base, so shared keys live once in the base file.
    With `environment=None` (an undeclared project) the overlay is never
    consulted and resolution is byte-identical to the two-level channel. An
    empty-string `environment` counts as unset (the ADR-0034 posture).

    `secrets_path` (or `repo_root`) pins the file location for tests and for
    callers that already resolved the project; absent both, the files are found
    by walking up from cwd to the directory holding `.praxis/`. The overlay is
    always resolved as a sibling of the base file (`<base name>.<env>`), so a
    pinned base pins the overlay too.

    Raises `MissingCredential(key, environment)` when no source provides the
    key. The raised value names only the absent KEY (and, with an environment
    selected, the consulted overlay file), never any present value.
    """
    if environment == "":
        environment = None

    env = os.environ if environ is None else environ
    if key in env and env[key] != "":
        return env[key]

    path = secrets_path
    if path is None:
        root = repo_root if repo_root is not None else _find_repo_root()
        if root is not None:
            path = root / SECRETS_FILENAME
    if path is not None:
        if environment is not None:
            overlay = path.with_name(f"{path.name}.{environment}")
            overlay_secrets = load_secrets_file(overlay)
            if key in overlay_secrets:
                return overlay_secrets[key]
        file_secrets = load_secrets_file(path)
        if key in file_secrets:
            return file_secrets[key]

    raise MissingCredential(key, environment)


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
    secrets file) to `stream` (default stderr). With an environment on the
    exception (ADR-0035 decision 7) it also names the per-env overlay file that
    was consulted; with `environment=None` the message is byte-identical to the
    pre-ADR-0035 one. It echoes no value: only the key name and the placeholder
    append command appear. The caller exits non-zero (see `require_credential`);
    this helper only formats the message.
    """
    out = sys.stderr if stream is None else stream
    overlay_line = ""
    if exc.environment is not None:
        overlay = overlay_filename(exc.environment)
        overlay_line = (
            f"  or add it to the {exc.environment!r} overlay:  "
            f'echo "{exc.key}={_VALUE_PLACEHOLDER}" >> {overlay}\n'
        )
    print(
        f"ERROR: missing credential {exc.key!r}.\n"
        f"  set it as an environment variable:  export {exc.key}=...\n"
        f"  or add it to {SECRETS_FILENAME}:        "
        f'echo "{exc.key}={_VALUE_PLACEHOLDER}" >> {SECRETS_FILENAME}\n'
        f"{overlay_line}"
        f"  ({SECRETS_FILENAME} is gitignored; the value is never committed or "
        f"logged.)",
        file=out,  # type: ignore[arg-type]
    )


def require_credential(
    key: str,
    *,
    environment: str | None = None,
    repo_root: Path | None = None,
    secrets_path: Path | None = None,
    environ: dict[str, str] | None = None,
    stream: object | None = None,
) -> str:
    """Console / CI accessor: return the credential or fail LOUDLY, non-zero.

    Wraps `get_credential` for the deterministic console surface (ADR-0019).
    `environment` selects the ADR-0035 per-env overlay (None: today's two-level
    resolution, unchanged). On success it returns the value. On a missing key
    it writes the loud, key-naming, no-prompt message (`fail_loud_missing`) and
    raises `SystemExit` with a non-zero code, so a CI run exits red with the
    key named and no interactive prompt. The secret value is never printed on
    either path.
    """
    try:
        return get_credential(
            key,
            environment=environment,
            repo_root=repo_root,
            secrets_path=secrets_path,
            environ=environ,
        )
    except MissingCredential as exc:
        fail_loud_missing(exc, stream=stream)
        raise SystemExit(2) from exc
