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

The exact `claude -p` invocation and the Playwright MCP config are settled live
against the real target app (ADR-0027 Open decision 4 / the live proof);
`extra_args` and `mcp_config_path` keep that wiring injectable without changing
the parse-and-raise contract this module guarantees.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

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

__all__ = ["make_claude_brain", "ClaudeBrainError"]


# The headless, non-interactive contract prepended to every per-goal prompt
# (ADR-0027 decision 3 + Pablo's constraint: the headless brain can never ask
# the user anything). The output contract names the exact JSON envelope
# `runner.regression._parse_executor_result` consumes.
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
    '  {"observations": [ {"value": "<one factual sentence>", "kind": '
    '"success|failure", "type": "behavioral|network|accessibility|text|url|'
    'visual", "present": true}, ... ], "actions": <int>, "tokens": null, '
    '"authenticated": <true|false>}\n'
    "Emit one observation per signal you checked. Set \"authenticated\" to false "
    "if the browser ended up logged out or you could not pass authentication.\n"
    "If a signal asks for a `structured check` (a count delta, or whether an id "
    "is present/absent after the action), add an `\"observed\"` object to THAT "
    "signal's observation carrying the raw data it names, and report the data you "
    "saw - do NOT decide yourself whether the check passed; the runner evaluates "
    "it. Shapes:\n"
    '  count delta -> "observed": {"before_count": <int>, "after_count": <int>}\n'
    '  membership  -> "observed": {"identifier": "<the concrete id you saw>", '
    '"present": <true|false>}\n'
    "Omit `observed` for a plain signal that has no `structured check` line.\n\n"
    "--- TASK ---\n"
)


class ClaudeBrainError(RuntimeError):
    """Raised when a `claude -p` invocation fails or returns unparseable output.

    A RuntimeError subclass so the engine's per-goal `except Exception` boxes it
    into a loud AggregateVerdict.ERROR (ADR-0023 decision 4); the message names
    the failure mode without echoing any secret.
    """


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

    The returned brain takes the engine's per-goal prompt, wraps it with the
    headless / non-interactive preamble and the output contract, runs `claude -p`
    capturing stdout, and returns the parsed observation dict. It RAISES on a
    non-zero exit, a timeout, or output that does not parse, so the engine boxes
    the failure into a loud per-goal ERROR rather than a false green.
    """

    def brain(prompt: str) -> dict[str, Any]:
        full_prompt = _HEADLESS_PREAMBLE + prompt
        argv = [claude_bin, "-p", full_prompt]
        if model:
            argv += ["--model", model]
        if mcp_config_path:
            # `--strict-mcp-config` so the run uses ONLY our Playwright MCP, not
            # whatever ambient MCP servers the user's global config defines: the
            # console runner must be deterministic about which browser it drives.
            argv += ["--mcp-config", mcp_config_path, "--strict-mcp-config"]
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
                f"claude binary not found: {claude_bin!r}. Install Claude Code or "
                f"pass --from-file."
            ) from exc
        finally:
            stop.set()
            hb.join(timeout=1)
        if proc.returncode != 0:
            # Name the failure without echoing stdout (it may carry app content);
            # the trimmed stderr tail is enough to diagnose.
            tail = (proc.stderr or "").strip()[-300:]
            raise ClaudeBrainError(
                f"claude -p exited {proc.returncode}: {tail}"
            )
        return _extract_observations(proc.stdout)

    return brain


def _merged_env(extra: dict[str, str] | None) -> dict[str, str] | None:
    """The current environment plus an optional hint, or None to inherit as-is.

    Returning None when there is no hint lets `subprocess.run` inherit the
    parent environment unchanged (the common case); a hint is layered on top so
    the `claude` subprocess keeps the user's PATH and subscription auth.
    """
    if not extra:
        return None
    import os

    merged = dict(os.environ)
    merged.update(extra)
    return merged
