"""The local `claude -p` console brain (ADR-0027 decisions 3, 5, 8).

These tests NEVER invoke a real `claude` binary: `subprocess.run` is
monkeypatched, so the brain stays testable with no Claude Code present, the same
way ADR-0019 keeps the core brain-agnostic. They pin the parse-and-raise
contract the engine relies on to turn a failure into a loud per-goal ERROR.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import praxis.cli.claude_brain as claude_brain_mod
from praxis.auth_session import env_var_name, save_session
from praxis.cli.claude_brain import (
    _HEADLESS_PREAMBLE,
    ClaudeBrainError,
    _extract_observations,
    _resolve_claude_argv0,
    make_claude_brain,
    resolve_storage_state,
)
from praxis.model import AuthState


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


def test_unset_model_appends_no_model_flag(monkeypatch: Any) -> None:
    """ADR-0034 backward compatibility: with no model pinned anywhere, the brain
    appends NO `--model` at all, so the argv is byte-identical to the
    pre-ADR-0034 invocation (the claude CLI's own default model runs)."""
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain()("p")
    assert "--model" not in seen["argv"]
    # And the empty string counts as unset too (never `--model ""`).
    seen2 = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain(model="")("p")
    assert "--model" not in seen2["argv"]


def test_pinned_model_is_appended_verbatim(monkeypatch: Any) -> None:
    """The model value is passed through VERBATIM as `--model <value>`: no
    validation against a model-name list (names rot; the claude CLI is the
    authority and errors loudly on an unknown model, ADR-0034)."""
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain(model="some-future-model-name")("p")
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == "some-future-model-name"


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


# --- saved-session reuse via --storage-state (ADR-0026, ADR-0027) ---------
#
# These tests never start a browser and never invoke claude: subprocess.run is
# patched, so they pin only that the brain synthesizes the right MCP config (or
# routes the missing-session loud path) per goal's auth_state.


def _storage_states_in(config: dict[str, Any]) -> list[str]:
    """Pull every `--storage-state <path>` pair out of an MCP config's servers.

    The `--storage-state` flag lives inside the Playwright SERVER args (a server
    arg, not a claude flag), so a test reads the synthesized config dict and
    collects each path the flag points at.
    """
    out: list[str] = []
    for server in config["mcpServers"].values():
        args = server.get("args", [])
        for i, a in enumerate(args):
            if a == "--storage-state":
                out.append(args[i + 1])
    return out


def _capture_synth_config(monkeypatch: Any, proc: "_FakeProc") -> dict[str, Any]:
    """Patch subprocess.run to read the synthesized MCP config WHILE the run is
    in flight (before the brain deletes it in its finally) and stash the parsed
    config under `seen["mcp_config"]`, plus the argv.
    """
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeProc:
        seen["argv"] = argv
        if "--mcp-config" in argv:
            synth = argv[argv.index("--mcp-config") + 1]
            seen["mcp_config"] = json.loads(Path(synth).read_text())
        return proc

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_resolve_storage_state_skips_being_tested_and_anonymous(tmp_path: Path) -> None:
    # An auth-SUBJECT goal (being_tested) and an anonymous / unauth goal never
    # reuse a session (ADR-0027 decision 2): no role, no storage state.
    auth_dir = tmp_path / ".praxis.auth"
    save_session("user", {"cookies": [], "origins": []}, auth_dir=auth_dir)
    subject = AuthState(authenticated=True, scope="user", being_tested=True)
    anon = AuthState(authenticated=False, scope=None)
    for state in (subject, anon, None):
        res = resolve_storage_state(state, auth_dir=auth_dir, environ={})
        assert res.path is None
        assert res.missing_role is None


def test_resolve_storage_state_uses_the_file_for_a_precondition_goal(tmp_path: Path) -> None:
    auth_dir = tmp_path / ".praxis.auth"
    path = save_session("user", {"cookies": [], "origins": []}, auth_dir=auth_dir)
    state = AuthState(authenticated=True, scope="user")  # precondition (default)
    res = resolve_storage_state(state, auth_dir=auth_dir, environ={})
    assert res.missing_role is None
    assert res.path == str(path)
    assert res.is_tempfile is False  # the file is read in place, never copied


def test_resolve_storage_state_env_wins_and_materializes_a_tempfile(tmp_path: Path) -> None:
    # CI supplies the session as a runner secret with NO file: env wins and the
    # raw JSON is materialized to a temp file so --storage-state has a path
    # (ADR-0026 decision 3). The temp file carries the env session, not the file.
    auth_dir = tmp_path / ".praxis.auth"
    save_session("user", {"cookies": [{"name": "from-file"}]}, auth_dir=auth_dir)
    env_session = {"cookies": [{"name": "from-env"}], "origins": []}
    state = AuthState(authenticated=True, scope="user")
    res = resolve_storage_state(
        state, auth_dir=auth_dir,
        environ={env_var_name("user"): json.dumps(env_session)},
    )
    assert res.missing_role is None
    assert res.is_tempfile is True
    assert json.loads(Path(res.path).read_text()) == env_session
    Path(res.path).unlink()


def test_resolve_storage_state_missing_session_names_the_role(tmp_path: Path) -> None:
    # An authenticated precondition goal with no env var and no file is the loud
    # missing-session case: the role is named, no path is produced (the brain
    # routes this to AUTH-EXPIRED, never a silent logged-out run).
    auth_dir = tmp_path / ".praxis.auth"  # empty
    state = AuthState(authenticated=True, scope="admin")
    res = resolve_storage_state(state, auth_dir=auth_dir, environ={})
    assert res.path is None
    assert res.missing_role == "admin"


def test_brain_injects_storage_state_for_an_authenticated_non_subject_goal(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """An authenticated precondition goal injects --storage-state pointing at the
    resolved session, inside a synthesized MCP config (ADR-0026, ADR-0027)."""
    auth_dir = tmp_path / ".praxis.auth"
    session_path = save_session("user", {"cookies": [], "origins": []}, auth_dir=auth_dir)
    state = AuthState(authenticated=True, scope="user")

    import praxis.cli.claude_brain as cb

    # Pin the resolution to this temp auth dir regardless of cwd.
    orig = cb.resolve_storage_state
    monkeypatch.setattr(
        cb, "resolve_storage_state",
        lambda s, **kw: orig(s, auth_dir=auth_dir, environ={}),
    )
    seen = _capture_synth_config(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    brain = make_claude_brain(session_for_goal=lambda: state)
    out = brain("p")
    assert out == _OBS
    # The synthesized MCP config carries --storage-state at the saved file.
    assert "--mcp-config" in seen["argv"]
    assert _storage_states_in(seen["mcp_config"]) == [str(session_path)]


def test_brain_does_not_inject_storage_state_for_a_being_tested_goal(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """An auth-SUBJECT goal performs a real login and does NOT load a session
    (ADR-0027 decision 2): no --storage-state, no synthesized config."""
    auth_dir = tmp_path / ".praxis.auth"
    save_session("user", {"cookies": [], "origins": []}, auth_dir=auth_dir)
    state = AuthState(authenticated=True, scope="user", being_tested=True)

    import praxis.cli.claude_brain as cb
    orig = cb.resolve_storage_state
    monkeypatch.setattr(
        cb, "resolve_storage_state",
        lambda s, **kw: orig(s, auth_dir=auth_dir, environ={}),
    )
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain(session_for_goal=lambda: state, mcp_config_path=None)("p")
    assert "--storage-state" not in json.dumps(seen["argv"])
    # No mcp-config is synthesized when there is no base and no session.
    assert "--mcp-config" not in seen["argv"]


def test_brain_does_not_inject_storage_state_for_an_anonymous_goal(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """An anonymous / non-authenticated goal never loads a session."""
    state = AuthState(authenticated=False, scope=None)
    seen = _patch_run(monkeypatch, _FakeProc(stdout=json.dumps(_OBS)))
    make_claude_brain(session_for_goal=lambda: state, mcp_config_path=None)("p")
    assert "--storage-state" not in json.dumps(seen["argv"])


def test_brain_surfaces_auth_expired_when_session_missing_instead_of_running(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """A missing session for an authenticated goal surfaces the loud path:
    authenticated=False with no observations, naming the role, and the browser
    is NEVER driven (subprocess.run must not be called), so the engine routes it
    to AUTH-EXPIRED rather than a silent logged-out REGRESSED."""
    auth_dir = tmp_path / ".praxis.auth"  # empty: no session for the role
    state = AuthState(authenticated=True, scope="admin")

    import praxis.cli.claude_brain as cb
    orig = cb.resolve_storage_state
    monkeypatch.setattr(
        cb, "resolve_storage_state",
        lambda s, **kw: orig(s, auth_dir=auth_dir, environ={}),
    )

    called = {"run": False}

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeProc:
        called["run"] = True
        return _FakeProc(stdout=json.dumps(_OBS))

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = make_claude_brain(session_for_goal=lambda: state)("p")
    assert called["run"] is False  # the browser was never driven
    assert out["authenticated"] is False
    assert out["observations"] == []
    assert any("admin" in n for n in out["notes"])


def test_preamble_documents_the_secrets_login_path() -> None:
    """The complementary fix: the console brain preamble tells the agent
    `.praxis.secrets` exists and how to use it for a fresh login (mirroring the
    skill text), so an authenticated goal with no session can still log in."""
    assert ".praxis.secrets" in _HEADLESS_PREAMBLE
    # The session secret rule carries over: never write secrets under .praxis/.
    assert ".praxis/" in _HEADLESS_PREAMBLE


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
