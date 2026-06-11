"""The local `claude -p` console brain (ADR-0027 decisions 3, 5, 8).

These tests NEVER invoke a real `claude` binary: `subprocess.run` is
monkeypatched, so the brain stays testable with no Claude Code present, the same
way ADR-0019 keeps the core brain-agnostic. They pin the parse-and-raise
contract the engine relies on to turn a failure into a loud per-goal ERROR.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

import praxis.cli.claude_brain as claude_brain_mod
from praxis.cli.claude_brain import (
    _HEADLESS_PREAMBLE,
    ClaudeBrainError,
    _extract_observations,
    _resolve_claude_argv0,
    make_claude_brain,
)


def test_preamble_documents_the_structured_check_observed_payload() -> None:
    """ADR-0031: the emit envelope must let an agent report a check's raw data
    via an `observed` object, or a `claude -p` regress run could not confirm a
    structured-check signal and it would fail closed (a false REGRESSED)."""
    assert "observed" in _HEADLESS_PREAMBLE
    assert "before_count" in _HEADLESS_PREAMBLE
    assert "after_count" in _HEADLESS_PREAMBLE
    assert "identifier" in _HEADLESS_PREAMBLE
    # The grounding contract: report the data, do not self-judge the verdict.
    assert "the runner evaluates" in _HEADLESS_PREAMBLE

_OBS = {
    "observations": [
        {"value": "dashboard renders", "kind": "success",
         "type": "behavioral", "present": True},
    ],
    "actions": 3,
    "tokens": None,
    "authenticated": True,
}


class _FakeProc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_run(monkeypatch: Any, proc: _FakeProc) -> dict[str, Any]:
    """Patch subprocess.run to return `proc` and capture the argv it was called
    with, so a test can assert the invocation shape without a real claude."""
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeProc:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_success_returns_the_observation_dict(monkeypatch: Any) -> None:
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    brain = make_claude_brain()
    out = brain("GOAL (dashboard): reach the dashboard")
    assert out == _OBS
    # The per-goal prompt is wrapped with the headless / non-interactive preamble.
    full_prompt = seen["argv"][2]
    assert "NON-INTERACTIVE" in full_prompt
    assert "GOAL (dashboard)" in full_prompt
    assert seen["argv"][:2] == ["claude", "-p"]


def test_observation_object_recovered_from_surrounding_prose(monkeypatch: Any) -> None:
    noisy = (
        "Let me drive the app.\nI checked the dashboard.\n"
        "Here is my result:\n```json\n" + json.dumps(_OBS) + "\n```\nDone."
    )
    _patch_run(monkeypatch, _FakeProc(stdout=noisy))
    brain = make_claude_brain()
    assert brain("p")["observations"][0]["value"] == "dashboard renders"


def test_claude_p_json_envelope_result_is_unwrapped(monkeypatch: Any) -> None:
    envelope = {"type": "result", "result": json.dumps(_OBS), "is_error": False}
    _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(envelope)))
    brain = make_claude_brain()
    assert brain("p") == _OBS


def test_non_zero_exit_raises(monkeypatch: Any) -> None:
    _patch_run(monkeypatch, _FakeProc(stderr="boom", returncode=2))
    brain = make_claude_brain()
    with pytest.raises(ClaudeBrainError) as exc:
        brain("p")
    assert "exited 2" in str(exc.value)


def test_timeout_raises(monkeypatch: Any) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeProc:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)
    brain = make_claude_brain(timeout_seconds=5)
    with pytest.raises(ClaudeBrainError) as exc:
        brain("p")
    assert "timed out" in str(exc.value)


def test_missing_binary_raises_with_actionable_message(monkeypatch: Any) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> _FakeProc:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", fake_run)
    brain = make_claude_brain()
    with pytest.raises(ClaudeBrainError) as exc:
        brain("p")
    assert "not found" in str(exc.value)
    assert "--from-file" in str(exc.value)


def test_empty_or_unparseable_output_raises(monkeypatch: Any) -> None:
    brain = make_claude_brain()
    _patch_run(monkeypatch, _FakeProc(stdout="   "))
    with pytest.raises(ClaudeBrainError):
        brain("p")
    _patch_run(monkeypatch, _FakeProc(stdout="I drove the app but emit nothing."))
    with pytest.raises(ClaudeBrainError):
        brain("p")


def test_headed_flag_layers_an_env_hint(monkeypatch: Any) -> None:
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain(headed=True)("p")
    env = seen["kwargs"]["env"]
    assert env is not None and env.get("PRAXIS_BROWSER_HEADED") == "1"
    # Headless default inherits the environment unchanged (env=None).
    seen2 = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain()("p")
    assert seen2["kwargs"]["env"] is None


def test_model_and_mcp_config_flags_are_forwarded(monkeypatch: Any) -> None:
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain(model="claude-opus-4-8", mcp_config_path="/tmp/mcp.json")("p")
    argv = seen["argv"]
    assert "--model" in argv and "claude-opus-4-8" in argv
    assert "--mcp-config" in argv and "/tmp/mcp.json" in argv
    # An mcp-config is used strictly (only our Playwright MCP, not ambient ones).
    assert "--strict-mcp-config" in argv


def test_headless_brain_bypasses_permission_prompts(monkeypatch: Any) -> None:
    """The headless brain runs non-interactive, so it must pre-grant tool
    permissions: a permission prompt would hang a run with no human to answer
    (ADR-0027 decision 8 / Pablo's "the brain can never ask")."""
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain()("p")
    argv = seen["argv"]
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


def test_extract_observations_prefers_the_last_object(monkeypatch: Any) -> None:
    # Two objects in the stream: the LAST with `observations` is the answer.
    first = json.dumps({"note": "thinking", "observations": []})
    last = json.dumps(_OBS)
    got = _extract_observations(f"{first}\nfinal:\n{last}")
    assert got == _OBS


# --- Windows npm `.cmd` shim launch robustness ----------------------------


def test_windows_cmd_shim_is_launched_through_the_command_interpreter(
    monkeypatch: Any,
) -> None:
    """On Windows, `npm install -g @anthropic-ai/claude-code` installs `claude`
    as a `claude.cmd` BATCH SHIM. `shutil.which` finds it (the preflight passes),
    but a bare `subprocess.run(["claude", ...])` cannot launch a batch file. The
    brain must resolve the shim and route it through the command interpreter so
    it actually starts, instead of raising FileNotFoundError."""
    monkeypatch.setattr(claude_brain_mod.os, "name", "nt")
    shim = r"C:\Users\dev\AppData\Roaming\npm\claude.cmd"
    monkeypatch.setattr(
        claude_brain_mod.shutil, "which",
        lambda name: shim if name == "claude" else None,
    )
    monkeypatch.setattr(
        claude_brain_mod.os, "environ", {"COMSPEC": r"C:\Windows\System32\cmd.exe"}
    )

    prefix = _resolve_claude_argv0("claude")
    # Routed through %COMSPEC% /c <full shim path>, not a bare "claude".
    assert prefix == [r"C:\Windows\System32\cmd.exe", "/c", shim]

    # End to end: the launched argv starts with the interpreter + /c + shim, then
    # the usual `-p <prompt>`, so the subprocess can actually run the batch shim.
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    out = make_claude_brain()("GOAL (x): reach x")
    assert out == _OBS
    argv = seen["argv"]
    assert argv[:3] == [r"C:\Windows\System32\cmd.exe", "/c", shim]
    assert argv[3] == "-p"
    assert "GOAL (x)" in argv[4]


def test_windows_native_exe_is_launched_by_resolved_path(monkeypatch: Any) -> None:
    """A native `claude.exe` on Windows is launched by its resolved full path
    (not routed through cmd, and not re-searched on PATH inside subprocess)."""
    monkeypatch.setattr(claude_brain_mod.os, "name", "nt")
    exe = r"C:\Program Files\claude\claude.exe"
    monkeypatch.setattr(
        claude_brain_mod.shutil, "which",
        lambda name: exe if name == "claude" else None,
    )
    assert _resolve_claude_argv0("claude") == [exe]


def test_posix_launch_prefix_is_unchanged(monkeypatch: Any) -> None:
    """On POSIX the resolution is a no-op: the launch prefix is just the bare
    binary name, so the existing argv shape (`["claude", "-p", ...]`) and every
    other test stay valid. We never call shutil.which on POSIX."""
    monkeypatch.setattr(claude_brain_mod.os, "name", "posix")

    def _boom(_name: str) -> str:
        raise AssertionError("shutil.which must not be consulted on POSIX")

    monkeypatch.setattr(claude_brain_mod.shutil, "which", _boom)
    assert _resolve_claude_argv0("claude") == ["claude"]


def test_windows_shim_not_found_falls_back_to_bare_name(monkeypatch: Any) -> None:
    """If which() finds nothing on Windows, the prefix is the bare name so the
    caller's subprocess raises the normal FileNotFoundError, which the brain maps
    to the actionable ClaudeBrainError (never a silent pass)."""
    monkeypatch.setattr(claude_brain_mod.os, "name", "nt")
    monkeypatch.setattr(claude_brain_mod.shutil, "which", lambda _name: None)
    assert _resolve_claude_argv0("claude") == ["claude"]
