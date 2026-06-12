"""The local `claude -p` console brain (ADR-0027 decisions 3, 5, 8).

This is the THIRD execution path for the brain seam (`runner.engine.Brain`,
`Callable[[str], dict]`): the SAME Claude Code subscription reasoning the
interactive skill uses, on NO API key, delivered through a HEADLESS console
process (`claude -p`) instead of an interactive session. It makes the console
`praxis regress` / `praxis explore` self-driving (ADR-0027 Finding A), replacing
the retired paste-on-stdin prompt.

The brain shells out with stdlib `subprocess` only (AGENTS.md: ask before any
new dep; ADR-0027 forbidden alternatives: stdlib subprocess), gives the agent a
Playwright MCP so it can drive the browser, passes the per-goal prompt the
engine already built, and captures the agent's observation JSON back in the
SAME shape `_executor_from_file` returns. It lives in the CLI layer; `runner/`
and `model/` never import it, so `import praxis` still works with no `claude`
binary present (ADR-0019 brain-agnostic core).

Headless is the default (ADR-0027 decision 5); `headed=True` shows the browser.
The agent runs NON-INTERACTIVE: the wrapped prompt states it cannot ask the user
anything and must never wait for input, so a goal blocked on a 2FA code or a
confirmation (ADR-0027 decision 8) emits what it observed and stops, it never
hangs. Any failure (non-zero exit, timeout, output that does not parse as the
observation JSON) RAISES, so the engine's per-goal try/except turns it into a
loud per-goal ERROR (ADR-0023 decision 4); the brain never returns a green
sentinel on failure.

For an authenticated goal whose login is a PRECONDITION (not the subject under
test), the brain loads the saved Playwright storage state for the goal's role
and runs the goal authenticated WITHOUT a fresh login, hence without a fresh 2FA
(ADR-0026 decisions 1, 3; ADR-0027 decision 2). The session is loaded through the
EXISTING `auth_session` helpers (env wins over file, ADR-0026 decision 3); when
the resolved session lives only in the env var `PRAXIS_AUTH_STATE_<ROLE>` and not
on disk (the CI runner-secret case) the brain materializes it to a TEMP file so
`@playwright/mcp --storage-state <path>` can read it. The temp file is created
with `0600` and removed after the run, so the session secret never leaks to a
persistent path or a log. A goal whose `auth_state.being_tested` is true (the
login IS the test) does NOT load a session: it performs a real login every run
(ADR-0027 decision 2). An anonymous / non-authenticated goal never loads one.

When an authenticated precondition goal needs a session but none is resolvable
(no env var, no file: `auth_session.MissingSession`), the brain does NOT silently
run logged out and produce a false REGRESSED (ADR-0026 decision 5, ADR-0027
decision 8). It short-circuits WITHOUT driving the browser and returns an
observation dict with `authenticated: False`, no success observations, and a note
naming the role, so the engine's `classify_goal` routes the goal to the loud
AUTH-EXPIRED verdict (it expects an authenticated scope but the run observed a
logged-out browser), never a false green and never a false red. The session VALUE
never crosses into the note: only the role name is named.

The exact `claude -p` invocation and the Playwright MCP config are settled live
against the real target app (ADR-0027 Open decision 4 / the live proof);
`extra_args` and `mcp_config_path` keep that wiring injectable without changing
the parse-and-raise contract this module guarantees.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..auth_session import (
    MissingSession,
    env_var_name,
    load_session_for_role,
    role_for_session_reuse,
    session_file_path,
)

if TYPE_CHECKING:
    from ..model import AuthState

# Live progress while the claude -p subprocess blocks (ADR-0027 decision 6 /
# Pablo's feedback: a silent multi-minute run reads as broken). On a real
# terminal a braille spinner animates in place; piped / captured output falls
# back to a plain line every _HEARTBEAT_SECONDS so logs are not flooded with
# carriage returns. The subprocess captures its own stdout for the observation
# JSON; this is the PARENT printing to its own stderr, so it never pollutes the
# parsed output.
#
# The frames are the braille "dots" spinner (the Claude Code look). They are
# written as \u escape sequences, so THIS SOURCE FILE stays pure ASCII (the
# prohibited-characters hook sees no glyph); the unicode only exists at runtime
# in the terminal animation. Pablo explicitly authorized this one bypass of the
# no-decorative-unicode rule, scoped to the terminal spinner (a runtime UI
# animation, not prose / docs / committed text).
_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
_SPINNER_INTERVAL = 0.1  # s, TTY refresh (smooth braille spin)
_HEARTBEAT_SECONDS = 30  # s, non-TTY plain-line cadence
_SPINNER_COLOR = "\x1b[36m"  # cyan (ANSI, ASCII escape)
_SPINNER_RESET = "\x1b[0m"

__all__ = [
    "make_claude_brain",
    "ClaudeBrainError",
    "StorageStateResolution",
    "resolve_storage_state",
]


# The headless, non-interactive contract prepended to every per-goal prompt
# (ADR-0027 decision 3 + Pablo's constraint: the headless brain can never ask
# the user anything). The output contract names the exact JSON envelope
# `runner.regression._parse_executor_result` consumes.
#
# The per-run "App under test: <base_url>" line (ADR-0035 decision 3) is
# DELIBERATELY NOT part of this preamble: the prompt renderers
# (`runner/prompts.py`) are the single injection point, so the per-goal TASK
# section below the preamble already carries the line whenever the CLI selected
# a declared environment. Injecting it here too would duplicate the line on the
# console path while the skill-driver path got it once, and a second injection
# point is a second place the undeclared-project byte-identity bar can break.
# The preamble stays constant across goals AND across environments.
_HEADLESS_PREAMBLE = (
    "You are running HEADLESS and NON-INTERACTIVE as a QA test runner. There is "
    "NO human watching and NO one to answer you. Do NOT ask any question, do NOT "
    "wait for input, and do NOT pause for a confirmation, a password, or a 2FA "
    "code. If you hit a wall you cannot pass without a human (for example an "
    "emailed 2FA code), STOP and emit what you observed so far, including that "
    "you could not authenticate, rather than waiting.\n\n"
    "Drive the app with the Playwright MCP browser tools. Regenerate your own "
    "steps; do NOT replay recorded steps.\n\n"
    "When you are done, output EXACTLY ONE JSON object as the LAST thing you "
    "print, on its own, and nothing after it:\n"
    '  {"confirmations": [ {"ref": "S1", "present": true, "evidence": "<the '
    'concrete detail you actually saw: the literal text, status, route, '
    'count>"}, ... ], "observations": [ {"value": "<one factual sentence>", '
    '"kind": "success|failure", "type": "behavioral|network|accessibility|'
    'text|url|visual", "present": true}, ... ], "actions": <int>, '
    '"tokens": null, "authenticated": <true|false>}\n'
    "Answer every signal the task enumerates with a ref (S1..Sn success, "
    "F1..Fm failure) in `confirmations`: one entry per ref. NEVER tick a "
    "signal just to complete the checklist; if you cannot ground it, report "
    "present: false (or omit it), never fabricate. `evidence` is MANDATORY "
    "for present: true (an empty evidence is VOID and counts as unconfirmed) "
    "and must be in your own words - do NOT copy the signal's wording; report "
    "what YOU saw. Do not restate the signal text anywhere else: the runner "
    "binds your answer to the signal by the ref alone.\n"
    "If a signal asks for a `structured check` (a count delta, or whether an id "
    "is present/absent after the action), add an `\"observed\"` object to THAT "
    "ref's confirmation entry carrying the raw data it names, and report the "
    "data you saw - do NOT decide yourself whether the check passed; the runner "
    "evaluates it. Shapes:\n"
    '  count delta -> "observed": {"before_count": <int>, "after_count": <int>}\n'
    '  membership  -> "observed": {"identifier": "<the concrete id you saw>", '
    '"present": <true|false>}\n'
    "Omit `observed` for a plain signal that has no `structured check` line.\n"
    "Use `observations` only for anything real you saw BEYOND the enumerated "
    "signals (extra failure evidence, a healthy equivalent). Set "
    "\"authenticated\" to false if the browser ended up logged out or you "
    "could not pass authentication.\n\n"
    "If the goal needs you to log in fresh (no saved session was injected for "
    "this run), log in the way a QA tester does: read the credentials from the "
    "gitignored `.praxis.secrets` file at the repo root (an environment variable "
    "wins over the file) and type them into the login form. NEVER write a "
    "credential, cookie, token, session id, or 2FA code into any file under "
    "`.praxis/`. If a needed credential is absent you cannot ask anyone (you are "
    "headless): emit what you observed with \"authenticated\": false and stop, do "
    "NOT guess a credential.\n\n"
    "--- TASK ---\n"
)


class ClaudeBrainError(RuntimeError):
    """Raised when a `claude -p` invocation fails or returns unparseable output.

    A RuntimeError subclass so the engine's per-goal `except Exception` boxes it
    into a loud AggregateVerdict.ERROR (ADR-0023 decision 4); the message names
    the failure mode without echoing any secret.
    """


# --- saved-session reuse for the console brain (ADR-0026, ADR-0027) -------
#
# A goal whose login is a PRECONDITION (not the subject under test) runs
# authenticated by reusing the saved Playwright storage state instead of doing a
# fresh login: `@playwright/mcp` reads it natively via `--storage-state <path>`
# (ADR-0026 decision 1). The role a goal reuses a session for is the ADR-0017
# `auth_state.scope`, resolved by the EXISTING `role_for_session_reuse` helper,
# which already returns None for a `being_tested` (auth-subject) goal and for an
# anonymous / non-authenticated goal, so those goals never load a session
# (ADR-0027 decision 2). The session VALUE is resolved through the EXISTING
# `load_session_for_role` (env wins over file, ADR-0026 decision 3); this module
# never re-implements that resolution.


class StorageStateResolution:
    """The per-goal outcome of resolving a `--storage-state` for the brain.

    Exactly one of three shapes (mutually exclusive):

      - `path` set, `missing_role` None: a storage-state file the brain points
        `@playwright/mcp --storage-state` at. `is_tempfile` marks an env-var
        session materialized to a temp file (the CI runner-secret case, ADR-0026
        decision 3) that the brain deletes after the run; a False `is_tempfile`
        is the gitignored `.praxis.auth/<role>.json` file, left in place.
      - `path` None, `missing_role` set: the goal needs an authenticated session
        for that role but none is resolvable (no env var, no file). The brain
        must NOT run logged out; it surfaces the loud AUTH-EXPIRED path naming
        the role (ADR-0026 decision 5, ADR-0027 decision 8).
      - both None: the goal needs no saved session (anonymous, non-authenticated,
        or `being_tested`); the brain drives a normal run (a fresh login if the
        goal itself requires one).

    Only role NAMES and a file PATH ever live here; a session VALUE never does
    (ADR-0026: the session is a secret named only by role).
    """

    __slots__ = ("path", "is_tempfile", "missing_role")

    def __init__(
        self,
        *,
        path: str | None = None,
        is_tempfile: bool = False,
        missing_role: str | None = None,
    ) -> None:
        self.path = path
        self.is_tempfile = is_tempfile
        self.missing_role = missing_role


def resolve_storage_state(
    auth_state: "AuthState | None",
    *,
    environment: str | None = None,
    repo_root: Path | None = None,
    auth_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> StorageStateResolution:
    """Resolve the `--storage-state` a goal's run should use, or its absence.

    Decides per goal (ADR-0026, ADR-0027 decision 2):

      1. `role_for_session_reuse(auth_state)` is the role a run MAY reuse a saved
         session for; it is None for a `being_tested` auth-subject goal (a real
         login every run) and for an anonymous / non-authenticated goal. No role
         -> no session needed (`StorageStateResolution()` with both fields None).
      2. With a role, prefer a session FILE on disk so `--storage-state` reads it
         directly. The env var WINS over the file (ADR-0026 decision 3), so when
         the env var is set the file path is NOT used even if it exists: the env
         session is materialized to a temp file (step 3) because `--storage-state`
         takes a path, not raw JSON.
      3. When only the env var carries the session (the CI runner-secret case, no
         file), materialize the resolved storage state to a 0600 temp file and
         return it with `is_tempfile=True` so the brain deletes it after the run.
      4. `auth_session.MissingSession` (no env var, no file) -> `missing_role` set
         so the brain surfaces the loud AUTH-EXPIRED path; never a silent
         logged-out run (ADR-0026 decision 5, AGENTS.md loud-over-silent).

    `environment` is the run's selected environment (ADR-0035 decision 7): with
    one selected, BOTH sources are env-scoped (`PRAXIS_AUTH_STATE_<ENV>_<ROLE>`,
    `.praxis.auth/<env>/<role>.json`) and the unscoped sources are deliberately
    NOT consulted (a storage state is domain-bound; an unscoped session present
    while an environment is selected is a `missing_role` outcome, never a silent
    wrong-domain reuse). With `environment=None` resolution is byte-identical to
    the unscoped channel; an empty string counts as unset.

    `repo_root` / `auth_dir` / `environ` are passed through to the EXISTING
    `auth_session` helpers for tests and pinned callers; the env-wins-over-file
    precedence and the env scoping are theirs, not re-implemented here.
    """
    environment = environment if environment else None
    role = role_for_session_reuse(auth_state)
    if role is None:
        return StorageStateResolution()

    env = os.environ if environ is None else environ
    env_present = bool(env.get(env_var_name(role, environment)))
    if not env_present:
        # No env override: the gitignored session file for THIS environment
        # (`.praxis.auth/<env>/<role>.json` when one is selected, the unscoped
        # `.praxis.auth/<role>.json` otherwise), if present, is what
        # `--storage-state` reads directly (no temp file). No cross-env or
        # unscoped fallback (ADR-0035 decision 7).
        file_path = session_file_path(
            role, environment=environment, repo_root=repo_root, auth_dir=auth_dir,
        )
        if file_path is not None and file_path.is_file():
            return StorageStateResolution(path=str(file_path), is_tempfile=False)
        return StorageStateResolution(missing_role=role)

    # The env var WINS over the file (ADR-0026 decision 3). `--storage-state`
    # needs a PATH, so materialize the env session to a temp file. `load_session_
    # for_role` reuses the env-wins resolution; a MissingSession here means the
    # env value is empty / malformed, which is still an unresolvable session.
    try:
        # Pass the original `environ` (a dict or None); `load_session_for_role`
        # reads `os.environ` itself when it is None, so env-wins precedence holds
        # without forcing the `_Environ` type across the boundary.
        session = load_session_for_role(
            role, environment=environment,
            repo_root=repo_root, auth_dir=auth_dir, environ=environ,
        )
    except MissingSession:
        return StorageStateResolution(missing_role=role)
    tmp_path = _materialize_session_tempfile(session)
    return StorageStateResolution(path=tmp_path, is_tempfile=True)


def _materialize_session_tempfile(session: dict[str, Any]) -> str:
    """Write a storage-state dict to a private temp file and return its path.

    The session is a secret (ADR-0026 decision 2): the file is created `0600`
    (owner-only) via `mkstemp`, the JSON is written to the fd, and nothing is
    printed. The caller deletes it after the run. Used only for the env-var /
    CI-runner-secret case where `--storage-state` needs a path, not raw JSON.
    """
    fd, name = tempfile.mkstemp(prefix="praxis-auth-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(session, fh)
    except Exception:
        # Never leave a half-written secret file behind on a write failure.
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return name


def _synthesize_mcp_config_with_storage_state(
    base_mcp_config_path: str | None, storage_state_path: str,
) -> str:
    """Write a per-run MCP config that adds `--storage-state <path>` to the
    Playwright server args, and return its path.

    `@playwright/mcp` reads the saved session via a `--storage-state <path>`
    SERVER argument (ADR-0026 decision 1: native support), so the flag lives in
    the MCP config's `args` array, not on the `claude -p` command line. This
    clones the project's base Playwright MCP config (the `--mcp-config` the run
    would otherwise use), appends `--storage-state <storage_state_path>` to every
    stdio server's `args`, and writes the result to a temp file the caller passes
    via `--mcp-config` for THIS goal only. The base config is left untouched.

    When there is no base config, a minimal default Playwright stdio server is
    synthesized (mirroring `cli.main`'s scaffolded template) so a run with a
    session but no declared MCP still loads `--storage-state`. The temp file is
    plain config (the storage-state PATH, not the session value), but it is
    created `0600` and deleted after the run for tidiness. Returns the temp path.
    """
    config: dict[str, Any] | None = None
    if base_mcp_config_path:
        try:
            loaded = json.loads(
                Path(base_mcp_config_path).read_text(encoding="utf-8")
            )
            if isinstance(loaded, dict):
                config = loaded
        except (OSError, json.JSONDecodeError):
            config = None
    if config is None:
        config = {
            "mcpServers": {
                "playwright": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@playwright/mcp@latest", "--headless"],
                    "env": {},
                }
            }
        }
    servers = config.get("mcpServers")
    if isinstance(servers, dict):
        for server in servers.values():
            if not isinstance(server, dict):
                continue
            args = server.get("args")
            if not isinstance(args, list):
                args = []
            # Idempotent: never double-add if a base config already carries it.
            if "--storage-state" not in args:
                args = [*args, "--storage-state", storage_state_path]
            server["args"] = args
    fd, name = tempfile.mkstemp(prefix="praxis-mcp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return name


def _auth_expired_observations(
    role: str, environment: str | None = None,
) -> dict[str, Any]:
    """The observation dict for a goal whose saved session is unresolvable.

    Returns `authenticated: False` with NO success observations and a note that
    names the ROLE (never a session value), so the engine's `classify_goal`
    routes the goal to the loud AUTH-EXPIRED verdict (it expected an
    authenticated scope but the run observed a logged-out browser, ADR-0026
    decision 5) rather than a false REGRESSED or a false green. The brain returns
    this WITHOUT driving the browser: with no session there is nothing to drive.

    With an `environment` selected (ADR-0035 decision 7) the note names the role
    AND the environment, with the env-scoped env-var name and file path in its
    hint, so the operator knows exactly which session to seed; with
    `environment=None` the note is byte-identical to the pre-ADR-0035 text. An
    empty string counts as unset.
    """
    environment = environment if environment else None
    if environment is None:
        hint = (
            f"no saved auth session for role {role!r}: set "
            f"{env_var_name(role)} to the storageState JSON, or save one to "
            f".praxis.auth/{role}.json (seed it with a teach login)."
        )
    else:
        hint = (
            f"no saved auth session for role {role!r} on environment "
            f"{environment!r}: set {env_var_name(role, environment)} to the "
            f"storageState JSON, or save one to "
            f".praxis.auth/{environment}/{role}.json (seed it with a teach "
            f"login against that environment; the unscoped session is "
            f"deliberately not used, ADR-0035)."
        )
    return {
        "observations": [],
        "actions": 0,
        "tokens": None,
        "authenticated": False,
        "notes": [
            f"{hint} The run did not drive the app logged out; this is "
            f"AUTH-EXPIRED, not a regression."
        ],
    }


def _resolve_claude_argv0(claude_bin: str) -> list[str]:
    """Resolve the launch prefix for `claude_bin` so it actually starts, on
    POSIX and on Windows.

    On Windows, `npm install -g @anthropic-ai/claude-code` installs `claude` as a
    `claude.cmd` (or `.bat`) BATCH SHIM, not a native `.exe`. `shutil.which`
    finds it (so the CLI preflight passes), but `subprocess.run(["claude", ...])`
    with the default `shell=False` cannot launch a batch file: the Win32
    `CreateProcess` it calls only runs real executables, so it raises
    `FileNotFoundError` even though the shim is right there on PATH. The robust
    fix is to resolve the shim to its full path and run it through the command
    interpreter (`%COMSPEC% /c claude.cmd ...`), which IS how a `.cmd` is meant to
    be invoked. We resolve the full path (rather than `shell=True` with a bare
    name) so no shell quoting touches the prompt argument that follows.

    On POSIX this is a no-op: `claude` is a normal executable (or a shebang
    script the kernel launches directly), so the launch prefix is just
    `[claude_bin]` and the existing behavior is unchanged.

    Returns the argv PREFIX (one or more tokens); the caller appends `-p`, the
    prompt, and the rest. The binary is never run here; this only builds argv.
    """
    if os.name != "nt":
        return [claude_bin]
    resolved = shutil.which(claude_bin)
    if resolved is None:
        # which() did not find it; let the caller's subprocess raise the normal
        # FileNotFoundError, which the brain maps to a clear ClaudeBrainError.
        return [claude_bin]
    if resolved.lower().endswith((".cmd", ".bat")):
        # Route a batch shim through the command interpreter so it launches.
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", resolved]
    # A native .exe (or a path which() resolved): launch the resolved path
    # directly so we do not re-search PATH inside subprocess.
    return [resolved]


def _iter_balanced_objects(text: str) -> list[str]:
    """Yield every top-level balanced `{...}` substring of `text`, in order.

    Used to recover the observation JSON when `claude -p` wraps it in prose or
    a code fence: we scan for brace-balanced candidates and let the caller try
    to parse each. String literals are respected so a `{` inside a quoted value
    does not throw the balance off.
    """
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start : i + 1])
                    start = -1
    return out


def _extract_observations(stdout: str) -> dict[str, Any]:
    """Recover the observation dict from `claude -p` stdout, or raise.

    Accepts three shapes, most-specific first: the raw observation object on its
    own; a `claude -p --output-format json` envelope whose `result` string
    carries the observation object; or the observation object embedded in prose
    / a code fence. The result MUST contain an `observations` key; anything else
    is ambiguous and raises rather than guessing (ADR-0027: a malformed output
    is a loud ERROR, never a silent green).
    """
    text = stdout.strip()
    if not text:
        raise ClaudeBrainError("claude -p produced no output")

    # Direct parse: the whole stdout is one JSON value.
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        if "observations" in whole:
            return whole
        # claude -p --output-format json envelope: the agent's text is in
        # `result`; recurse into it for the observation object.
        result = whole.get("result")
        if isinstance(result, str):
            return _extract_observations(result)

    # Embedded: scan brace-balanced candidates, last-wins (the final printed
    # object is the agent's answer per the output contract).
    for candidate in reversed(_iter_balanced_objects(text)):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "observations" in obj:
            return obj

    raise ClaudeBrainError(
        "claude -p output did not contain a parseable observations JSON object"
    )


def make_claude_brain(
    *,
    headed: bool = False,
    timeout_seconds: float | None = None,
    claude_bin: str = "claude",
    model: str | None = None,
    mcp_config_path: str | None = None,
    extra_args: list[str] | None = None,
    progress: Callable[[], tuple[str, str] | None] | None = None,
    session_for_goal: "Callable[[], AuthState | None] | None" = None,
    environment: str | None = None,
) -> Callable[[str], dict[str, Any]]:
    """Build a `Brain` that drives one goal through a headless `claude -p` run.

    `headed` shows the browser (default headless, ADR-0027 decision 5).
    `timeout_seconds` bounds the subprocess (the per-goal wall slice maps here,
    ADR-0027 decision 4); a timeout raises and becomes a loud ERROR.
    `mcp_config_path` / `extra_args` inject the Playwright MCP wiring settled
    live (ADR-0027 Open decision 4) without changing this contract.

    `progress`, when given, is read once at the start of each goal's run and
    returns `(prefix, label)` for that goal (e.g. `("[1/2]", "create-welcome-
    popup")`), so the live spinner reads as a pytest-style
    `  [1/2] (spin) Running   create-welcome-popup   1:25` line that resolves
    into the goal's verdict line. When it is None (or returns None) the spinner
    falls back to the generic `driving the browser` line. The CLI installs it
    only for sequential runs (one in-place line, one goal at a time).

    `session_for_goal`, when given, is read once at the start of each goal's run
    and returns the goal's ADR-0017 `auth_state` (or None). The brain resolves it
    through `resolve_storage_state`: a precondition authenticated goal gets its
    saved Playwright storage state injected via a synthesized per-goal MCP config
    carrying `--storage-state <path>` (ADR-0026 decisions 1, 3); a `being_tested`
    auth-subject goal or an anonymous goal gets no session (ADR-0027 decision 2);
    a goal whose session is unresolvable short-circuits to the loud AUTH-EXPIRED
    observation WITHOUT driving the browser, never a silent logged-out run
    (ADR-0026 decision 5). The CLI sets the current goal in the same thread that
    will run it (sequential or the worker thread of a `--jobs > 1` run), so the
    read is thread-correct.

    `environment` is the run's SELECTED environment (ADR-0035 decision 7), the
    one the CLI resolved per its flag > PRAXIS_ENV > default_env precedence. It
    scopes the session resolution (`PRAXIS_AUTH_STATE_<ENV>_<ROLE>`, then
    `.praxis.auth/<env>/<role>.json`, with NO fallback to the unscoped sources:
    sessions are domain-bound) and the AUTH-EXPIRED note, which then names the
    role AND the environment. None (the undeclared project) keeps today's
    unscoped resolution and note byte-identical.

    The returned brain takes the engine's per-goal prompt, wraps it with the
    headless / non-interactive preamble and the output contract, runs `claude -p`
    capturing stdout, and returns the parsed observation dict. It RAISES on a
    non-zero exit, a timeout, or output that does not parse, so the engine boxes
    the failure into a loud per-goal ERROR rather than a false green.
    """

    def brain(prompt: str) -> dict[str, Any]:
        # Resolve the goal's saved session FIRST (ADR-0026, ADR-0027 decision 2):
        # a precondition authenticated goal reuses a saved storage state; an
        # auth-subject (`being_tested`) or anonymous goal does not; an
        # unresolvable session short-circuits loudly to AUTH-EXPIRED.
        auth_state = session_for_goal() if session_for_goal is not None else None
        resolution = resolve_storage_state(auth_state, environment=environment)
        if resolution.missing_role is not None:
            # No env var and no file for a goal that needs the session: do NOT
            # drive the browser logged out (a false REGRESSED). Return the
            # AUTH-EXPIRED-routing observation naming only the role (and the
            # selected environment, when there is one, ADR-0035 decision 7).
            return _auth_expired_observations(
                resolution.missing_role, environment,
            )

        # When a session resolved, synthesize a per-goal MCP config that adds
        # `--storage-state <path>` to the Playwright server args (the flag is a
        # SERVER arg, not a `claude -p` flag); else use the base config as-is.
        run_mcp_config = mcp_config_path
        synthesized_mcp_config: str | None = None
        if resolution.path is not None:
            synthesized_mcp_config = _synthesize_mcp_config_with_storage_state(
                mcp_config_path, resolution.path,
            )
            run_mcp_config = synthesized_mcp_config

        full_prompt = _HEADLESS_PREAMBLE + prompt
        # Resolve the launch prefix so a Windows npm `.cmd` shim actually starts
        # (a bare `["claude", ...]` cannot launch a batch file); POSIX is a no-op.
        argv = [*_resolve_claude_argv0(claude_bin), "-p", full_prompt]
        if model:
            argv += ["--model", model]
        if run_mcp_config:
            # `--strict-mcp-config` so the run uses ONLY our Playwright MCP, not
            # whatever ambient MCP servers the user's global config defines: the
            # console runner must be deterministic about which browser it drives.
            argv += ["--mcp-config", run_mcp_config, "--strict-mcp-config"]
        # Headless / non-interactive: there is no human to approve a tool call,
        # so a permission prompt would hang the run (the Finding-A failure on the
        # brain side; Pablo's constraint that the brain can never ask). Bypass
        # permission checks for this subprocess; the blast radius is bounded by
        # the low-privilege test account the run drives (ADR-0026 consequences).
        argv += ["--permission-mode", "bypassPermissions"]
        if extra_args:
            argv += list(extra_args)
        # `headed` is plumbed to the browser the MCP launches; until the MCP
        # config is settled live (Open decision 4) it travels as an env hint the
        # MCP server reads, never changing the parse-and-raise contract.
        env_hint = {"PRAXIS_BROWSER_HEADED": "1"} if headed else None
        # Progress feedback (ADR-0027 decision 6): announce the run immediately,
        # then show a live ASCII spinner with elapsed time while the subprocess
        # blocks, so a multi-minute headless browser run does not read as a hang.
        # The spinner animates in place ONLY on a real terminal (TTY); piped /
        # captured output (CI, a test) falls back to a plain line every
        # _HEARTBEAT_SECONDS so logs are not flooded with carriage returns. All of
        # this is the PARENT printing to its own stderr from a daemon thread; the
        # subprocess's own stdout is still captured for the observation JSON.
        mode = "headed" if headed else "headless"
        # Read the goal's progress label ONCE per run (the CLI sets it just
        # before this call, sequential mode). When present, the spinner renders
        # a pytest-style `[i/total] (spin) Running   <goal>   <clock>` line and
        # the verbose announce is suppressed (the running line already says it
        # all); when absent, keep the generic announce + `driving the browser`
        # spinner so the single-goal and non-TTY paths are unchanged.
        prog = progress() if progress is not None else None
        if prog is None:
            print(
                f"  [claude -p] driving the browser ({mode}) on your "
                f"subscription; this can take a few minutes...",
                file=sys.stderr, flush=True,
            )
        stop = threading.Event()
        is_tty = sys.stderr.isatty()

        def _spinner() -> None:
            start = time.monotonic()
            last_beat = 0.0
            i = 0
            interval = _SPINNER_INTERVAL if is_tty else 1.0
            while not stop.wait(interval):
                elapsed = int(time.monotonic() - start)
                clock = f"{elapsed // 60}:{elapsed % 60:02d}"
                if is_tty:
                    frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
                    i += 1
                    spin = f"{_SPINNER_COLOR}{frame}{_SPINNER_RESET}"
                    if prog is not None:
                        prefix, label = prog
                        # Pytest-style live line: `[i/total] (spin) Running
                        # <goal> <clock>`. `\x1b[2K` clears the whole line first
                        # so a shorter render never leaves a stale tail.
                        body = (f"  {prefix} {spin} Running   {label}   "
                                f"{clock}")
                    else:
                        body = (f"  {spin} driving the browser ({mode})   "
                                f"{clock}")
                    print(f"\r\x1b[2K{body}", end="", file=sys.stderr, flush=True)
                elif elapsed - last_beat >= _HEARTBEAT_SECONDS:
                    last_beat = float(elapsed)
                    if prog is not None:
                        prefix, label = prog
                        print(f"  {prefix} Running {label}... {clock}",
                              file=sys.stderr, flush=True)
                    else:
                        print(f"  still driving the browser... {clock}",
                              file=sys.stderr, flush=True)
            if is_tty:
                # Wipe the spinner line so the verdict output starts clean.
                print("\r\x1b[2K", end="", file=sys.stderr, flush=True)

        hb = threading.Thread(target=_spinner, daemon=True)
        hb.start()
        try:
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    env=_merged_env(env_hint),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ClaudeBrainError(
                    f"claude -p timed out after {timeout_seconds}s"
                ) from exc
            except FileNotFoundError as exc:
                raise ClaudeBrainError(
                    f"claude binary not found: {claude_bin!r}. Install Claude "
                    f"Code or pass --from-file."
                ) from exc
            finally:
                stop.set()
                hb.join(timeout=1)
            if proc.returncode != 0:
                # Name the failure without echoing stdout (it may carry app
                # content); the trimmed stderr tail is enough to diagnose.
                tail = (proc.stderr or "").strip()[-300:]
                raise ClaudeBrainError(
                    f"claude -p exited {proc.returncode}: {tail}"
                )
            return _extract_observations(proc.stdout)
        finally:
            # Always remove the per-goal temp files: the session storage state
            # is a secret (ADR-0026 decision 2), and the synthesized MCP config
            # is run-scoped. Both are removed whether the run succeeded, raised,
            # or timed out, so no session ever lingers on disk.
            if resolution.is_tempfile and resolution.path is not None:
                _unlink_quietly(resolution.path)
            if synthesized_mcp_config is not None:
                _unlink_quietly(synthesized_mcp_config)

    return brain


def _unlink_quietly(path: str) -> None:
    """Delete a temp file, ignoring an already-removed / missing file.

    Cleanup must never raise over an absent file (a double cleanup, or a file the
    OS reaped), so it can run unconditionally in a `finally` without masking the
    real result of the run.
    """
    try:
        os.unlink(path)
    except OSError:
        pass


def _merged_env(extra: dict[str, str] | None) -> dict[str, str] | None:
    """The current environment plus an optional hint, or None to inherit as-is.

    Returning None when there is no hint lets `subprocess.run` inherit the
    parent environment unchanged (the common case); a hint is layered on top so
    the `claude` subprocess keeps the user's PATH and subscription auth.
    """
    if not extra:
        return None
    merged = dict(os.environ)
    merged.update(extra)
    return merged
