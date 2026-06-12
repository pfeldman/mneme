"""CLI smoke tests.

Drive each verb through `praxis.cli.main` with controlled cwd + tempdir.
Cover the contract a user would hit on a real project: init creates the
expected layout, learn refuses agent-sourced seeds (ADR-0005), regress
fails when the agent reports a regression, status reads the projection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from praxis.cli import main


def _run(args, cwd: Path) -> int:
    """Run the CLI with chdir to `cwd` so project discovery works."""
    old = Path.cwd()
    os.chdir(cwd)
    try:
        return main(args)
    finally:
        os.chdir(old)


def _seed_login_yaml() -> str:
    """A minimal valid seed knowledge file for goal_id=login."""
    return """\
schema_version: "0"
goal_id: login
goal: a returning user can authenticate
target:
  app: testapp
  environment: local
success_signals:
  - type: behavioral
    value: a Sign out control is present after submitting valid credentials
    confidence: 1.0
    status: believed
    provenance:
      source_type: human
      source_id: pablo
      last_verified: "2026-06-07T00:00:00Z"
      observation_count: 1
failure_signals:
  - type: text
    value: an invalid credentials banner appears
    confidence: 1.0
    status: believed
    provenance:
      source_type: human
      source_id: pablo
      last_verified: "2026-06-07T00:00:00Z"
      observation_count: 1
meta:
  created_at: "2026-06-07T00:00:00Z"
  updated_at: "2026-06-07T00:00:00Z"
"""


# --- init -----------------------------------------------------------------


def test_init_creates_layout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = _run(["init", "--app", "tests", "--env", "local"], tmp_path)
    assert rc == 0
    pdir = tmp_path / ".praxis"
    # ADR-0021 decision 1 layout: config + knowledge + candidates + runs +
    # .praxisignore. The repo-root .gitignore (not a .praxis/.gitignore) carries
    # the ignore lines.
    assert (pdir / "config.yaml").exists()
    assert (pdir / "knowledge").is_dir()
    assert (pdir / "candidates").is_dir()
    assert (pdir / "runs").is_dir()
    assert (pdir / ".praxisignore").exists()
    assert (tmp_path / ".gitignore").exists()
    out = capsys.readouterr().out
    assert "initialized praxis project" in out


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    rc = _run(["init"], tmp_path)
    assert rc == 2


# --- init --environment / --default-env (ADR-0035 decision 9) ---------------


def test_init_environment_excludes_legacy_env_flag(tmp_path: Path) -> None:
    """The multi-env scaffold flags and the legacy single-env pair are
    mutually exclusive: mixing them errors loudly, NAMING both styles, and
    nothing is written to disk."""
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--environment", "prod=https://example.com",
              "--env", "local"], tmp_path)
    msg = str(exc.value)
    assert "--environment" in msg and "--default-env" in msg
    assert "--env" in msg and "--base-url" in msg
    assert not (tmp_path / ".praxis").exists()


def test_init_environment_excludes_legacy_base_url_flag(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--environment", "prod=https://example.com",
              "--base-url", "https://example.com"], tmp_path)
    msg = str(exc.value)
    assert "--environment" in msg and "--base-url" in msg
    assert not (tmp_path / ".praxis").exists()


def test_init_default_env_excludes_legacy_flags_too(tmp_path: Path) -> None:
    # `--default-env` alone already selects the multi-env style.
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--default-env", "prod", "--env", "local"], tmp_path)
    assert "--default-env" in str(exc.value)
    assert not (tmp_path / ".praxis").exists()


def test_init_default_env_must_name_a_declared_environment(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--environment", "prod=https://example.com",
              "--default-env", "dev2"], tmp_path)
    msg = str(exc.value)
    assert "dev2" in msg and "prod" in msg and "--default-env" in msg
    assert not (tmp_path / ".praxis").exists()


def test_init_default_env_requires_an_environment(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--default-env", "prod"], tmp_path)
    assert "--environment" in str(exc.value)
    assert not (tmp_path / ".praxis").exists()


def test_init_environment_spec_must_be_name_equals_url(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--environment", "prod"], tmp_path)
    assert "NAME=URL" in str(exc.value)
    assert not (tmp_path / ".praxis").exists()


def test_init_environment_name_must_round_trip(tmp_path: Path) -> None:
    """A name that cannot round-trip through the per-env file paths and the
    PRAXIS_AUTH_STATE_<ENV>_<ROLE> env-var channel is rejected at init time,
    so it never enters a committed config (ADR-0035 decision 9)."""
    for bad in ("dev-2", "dev.2", "dev 2", "dev/2"):
        with pytest.raises(SystemExit) as exc:
            _run(["init", "--environment", f"{bad}=https://x.example.com"],
                 tmp_path)
        msg = str(exc.value)
        assert bad in msg and "A-Za-z0-9_" in msg
    assert not (tmp_path / ".praxis").exists()


def test_init_environment_duplicate_names_are_loud(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--environment", "prod=https://a.example.com",
              "--environment", "prod=https://b.example.com"], tmp_path)
    assert "twice" in str(exc.value)
    # Two names that collide once uppercased into the env-var channel are
    # rejected too: PRAXIS_AUTH_STATE_PROD_<ROLE> could not tell them apart.
    with pytest.raises(SystemExit) as exc:
        _run(["init", "--environment", "prod=https://a.example.com",
              "--environment", "PROD=https://b.example.com"], tmp_path)
    assert "uppercased" in str(exc.value)
    assert not (tmp_path / ".praxis").exists()


def test_init_declared_next_steps_mention_the_env_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A declared init tells the user how to pick an environment per run; an
    undeclared init prints exactly the text it printed before (zero
    ceremony, ADR-0035)."""
    rc = _run(["init",
               "--environment", "dev2=https://dev2.example.com",
               "--environment", "prod=https://example.com",
               "--default-env", "dev2"], tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Environments declared: dev2, prod" in out
    assert "--env <name>" in out and "PRAXIS_ENV" in out
    assert ".praxis.secrets.<env>" in out


def test_init_undeclared_next_steps_do_not_mention_environments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = _run(["init"], tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Environments declared" not in out
    assert "PRAXIS_ENV" not in out


# --- learn ----------------------------------------------------------------


def test_learn_imports_a_seed_file(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    rc = _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    assert rc == 0
    out = tmp_path / ".praxis" / "knowledge" / "login.knowledge.yaml"
    assert out.exists()


def test_learn_rejects_goal_id_mismatch(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    rc = _run(["learn", "wrong-goal", "--from-file", str(seed)], tmp_path)
    assert rc == 2


def test_learn_refuses_agent_sourced_seed(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    yaml_text = _seed_login_yaml().replace("source_type: human",
                                            "source_type: agent")
    seed = tmp_path / "login.yaml"
    seed.write_text(yaml_text)
    rc = _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    assert rc == 2  # ADR-0005: seeds must be human/spec


# --- status ---------------------------------------------------------------


def test_status_summarizes_seeded_knowledge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    capsys.readouterr()  # clear earlier output
    rc = _run(["status"], tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "login" in out
    assert "success=" in out


# --- regress --------------------------------------------------------------


def test_regress_pass_path(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    obs_file = tmp_path / "agent.json"
    obs_file.write_text(json.dumps({
        "observations": [
            {
                "kind": "success", "type": "behavioral",
                "value": "a Sign out control is present after submitting valid credentials",
                "source_type": "agent", "source_id": "praxis-cli",
            },
        ],
        "actions": 5, "tokens": 1000, "visited_urls": []
    }))
    rc = _run(["regress", "--goal", "login",
                "--from-file", str(obs_file),
                "--budget-tokens", "5000"], tmp_path)
    assert rc == 0  # PASS -> exit 0
    # Reports land under the per-run dir (ADR-0021: runs/<timestamp>/).
    runs = tmp_path / ".praxis" / "runs"
    md_files = sorted(runs.glob("*/last-regress.md"))
    xml_files = sorted(runs.glob("*/last-regress.xml"))
    assert md_files and xml_files
    assert "**pass**" in md_files[-1].read_text()


def test_single_goal_regress_prints_verdict_on_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """ADR-0027 decision 6: a single-goal `praxis regress --goal X` shows the
    verdict ON THE CONSOLE (a tagged result line + a pytest-style tally), so a
    human does not have to open the markdown report."""
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    obs_file = tmp_path / "agent.json"
    obs_file.write_text(json.dumps({
        "observations": [{
            "kind": "success", "type": "behavioral",
            "value": "a Sign out control is present after submitting valid credentials",
            "source_type": "agent", "source_id": "praxis-cli",
        }],
        "actions": 5, "tokens": 1000,
    }))
    capsys.readouterr()
    rc = _run(["regress", "--goal", "login", "--from-file", str(obs_file)], tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "running 1 goal: login" in out
    assert "[ PASS ] login" in out
    assert "1/1 success signals matched" in out
    assert "1 passed" in out


def test_regress_fail_path_exits_nonzero(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    obs_file = tmp_path / "agent.json"
    obs_file.write_text(json.dumps({
        "observations": [
            {
                "kind": "failure", "type": "text",
                "value": "an invalid credentials banner appears",
                "source_type": "agent", "source_id": "praxis-cli",
            },
        ],
        "actions": 5, "tokens": 1000, "visited_urls": []
    }))
    rc = _run(["regress", "--goal", "login",
                "--from-file", str(obs_file),
                "--budget-tokens", "5000"], tmp_path)
    assert rc == 1  # any FAIL -> exit non-zero
    runs = tmp_path / ".praxis" / "runs"
    md_files = sorted(runs.glob("*/last-regress.md"))
    assert md_files
    md = md_files[-1].read_text()
    assert "**fail**" in md
    assert "regression" in md.lower()


# --- ADR-0027 decision 7: default console brain selection ------------------


def test_regress_without_claude_or_from_file_fails_loudly_not_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no `--from-file` and no `claude` on PATH, a console regress FAILS
    LOUDLY with an actionable message instead of hanging on stdin (ADR-0027
    decision 7, Finding A: the paste-on-stdin default is retired)."""
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    # `praxis.cli.main` (the function) shadows the submodule attribute; reach the
    # real module via sys.modules so the monkeypatch targets the right names.
    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    # No claude binary discoverable.
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit) as exc:
        _run(["regress"], tmp_path)
    msg = str(exc.value)
    assert "claude" in msg and "--from-file" in msg


def test_regress_defaults_to_claude_brain_when_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `claude` on PATH and no `--from-file`, the console regress drives the
    `claude -p` brain by default (ADR-0027 decision 7). We monkeypatch the brain
    factory to a fake so no real claude is invoked, and assert it was selected
    and the headless default flowed through."""
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    captured: dict = {}

    def fake_factory(**kwargs):
        captured.update(kwargs)

        def brain(prompt: str) -> dict:
            return {
                "observations": [{
                    "kind": "success", "type": "behavioral",
                    "value": "a Sign out control is present after submitting "
                             "valid credentials",
                    "source_type": "agent", "source_id": "praxis-cli",
                }],
                "actions": 1, "tokens": 10,
            }
        return brain

    monkeypatch.setattr(cli_mod, "make_claude_brain", fake_factory)
    rc = _run(["regress"], tmp_path)
    assert rc == 0
    # The claude brain was selected with the headless default (no --headed).
    assert captured.get("headed") is False


def test_regress_uses_the_project_mcp_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project can declare its Playwright MCP once in .praxis/config.yaml
    (`mcp_config`), and a run with no --mcp-config picks it up, resolved absolute
    against the project root (ADR-0027). The flag still overrides it."""
    _run(["init", "--mcp-config", "playwright-mcp.json"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    captured: dict = {}

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return lambda prompt: {
            "observations": [{
                "kind": "success", "type": "behavioral",
                "value": "a Sign out control is present after submitting "
                         "valid credentials",
                "source_type": "agent", "source_id": "praxis-cli",
            }],
            "actions": 1, "tokens": 10,
        }

    monkeypatch.setattr(cli_mod, "make_claude_brain", fake_factory)
    rc = _run(["regress"], tmp_path)
    assert rc == 0
    # The config default flowed through, resolved absolute against the root.
    got = captured.get("mcp_config_path")
    assert got is not None
    assert Path(got).is_absolute()
    assert got.endswith("playwright-mcp.json")
    assert str(tmp_path) in got


# --- ADR-0034: brain model pin (--model flag > PRAXIS_BRAIN_MODEL env > -----
# --- config.yaml brain_model > unset = the claude CLI default) --------------


def test_resolve_brain_model_precedence_chain() -> None:
    """The resolution is a pure function in the CLI layer: each level alone
    pins, the chain is flag > env > config, unset everywhere is (None, None),
    and an empty string at any level counts as unset (ADR-0034)."""
    from praxis.cli.main import BRAIN_MODEL_ENV, _resolve_brain_model

    env = {BRAIN_MODEL_ENV: "from-env"}
    assert _resolve_brain_model("from-flag", "from-config", environ=env) == (
        "from-flag", "--model flag")
    assert _resolve_brain_model(None, "from-config", environ=env) == (
        "from-env", f"{BRAIN_MODEL_ENV} env")
    assert _resolve_brain_model(None, "from-config", environ={}) == (
        "from-config", "config.yaml brain_model")
    assert _resolve_brain_model(None, None, environ={}) == (None, None)
    # Empty values are unset: an exported-but-blank env var never masks the
    # committed pin, and a blank pin never produces `--model ""`.
    assert _resolve_brain_model("", "", environ={BRAIN_MODEL_ENV: ""}) == (
        None, None)


def test_project_config_reads_brain_model(tmp_path: Path) -> None:
    """The config reader: `brain_model` is read when present and None when the
    key is absent (the scaffolded config keeps it commented out)."""
    from praxis.cli.main import ProjectContext

    _run(["init"], tmp_path)
    assert ProjectContext(tmp_path).brain_model is None
    cfg = tmp_path / ".praxis" / "config.yaml"
    cfg.write_text(cfg.read_text() + "\nbrain_model: claude-sonnet-pin\n")
    assert ProjectContext(tmp_path).brain_model == "claude-sonnet-pin"


def _model_pin_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> dict:
    """Init + seed a project, make `claude` look available, clear any ambient
    PRAXIS_BRAIN_MODEL, and capture the kwargs the brain factory receives so a
    test can assert which model (if any) the resolution pinned."""
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.delenv("PRAXIS_BRAIN_MODEL", raising=False)
    captured: dict = {}

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return lambda prompt: {
            "observations": [{
                "kind": "success", "type": "behavioral",
                "value": "a Sign out control is present after submitting "
                         "valid credentials",
                "source_type": "agent", "source_id": "praxis-cli",
            }],
            "actions": 1, "tokens": 10,
        }

    monkeypatch.setattr(cli_mod, "make_claude_brain", fake_factory)
    return captured


def test_regress_model_flag_pins_the_brain_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _model_pin_setup(tmp_path, monkeypatch)
    rc = _run(["regress", "--model", "pin-from-flag"], tmp_path)
    assert rc == 0
    assert captured.get("model") == "pin-from-flag"


def test_regress_env_var_pins_when_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _model_pin_setup(tmp_path, monkeypatch)
    monkeypatch.setenv("PRAXIS_BRAIN_MODEL", "pin-from-env")
    rc = _run(["regress"], tmp_path)
    assert rc == 0
    assert captured.get("model") == "pin-from-env"


def test_regress_config_brain_model_pins_when_no_flag_no_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _model_pin_setup(tmp_path, monkeypatch)
    cfg = tmp_path / ".praxis" / "config.yaml"
    cfg.write_text(cfg.read_text() + "\nbrain_model: pin-from-config\n")
    rc = _run(["regress"], tmp_path)
    assert rc == 0
    assert captured.get("model") == "pin-from-config"


def test_regress_model_precedence_flag_over_env_over_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three levels set: the explicit per-run flag wins (ADR-0034)."""
    captured = _model_pin_setup(tmp_path, monkeypatch)
    cfg = tmp_path / ".praxis" / "config.yaml"
    cfg.write_text(cfg.read_text() + "\nbrain_model: pin-from-config\n")
    monkeypatch.setenv("PRAXIS_BRAIN_MODEL", "pin-from-env")
    rc = _run(["regress", "--model", "pin-from-flag"], tmp_path)
    assert rc == 0
    assert captured.get("model") == "pin-from-flag"


def test_regress_model_precedence_env_over_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env + config set, no flag: the env var (the CI channel) wins over the
    committed pin, mirroring the env-over-file secrets precedence."""
    captured = _model_pin_setup(tmp_path, monkeypatch)
    cfg = tmp_path / ".praxis" / "config.yaml"
    cfg.write_text(cfg.read_text() + "\nbrain_model: pin-from-config\n")
    monkeypatch.setenv("PRAXIS_BRAIN_MODEL", "pin-from-env")
    rc = _run(["regress"], tmp_path)
    assert rc == 0
    assert captured.get("model") == "pin-from-env"


def test_regress_unset_everywhere_pins_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No flag, no env, no config key: the brain factory receives model=None,
    so no `--model` is appended and the claude CLI default runs - today's
    behavior, byte-identical argv (ADR-0034 backward compatibility)."""
    captured = _model_pin_setup(tmp_path, monkeypatch)
    rc = _run(["regress"], tmp_path)
    assert rc == 0
    assert "model" in captured  # the factory was called with the kwarg
    assert captured["model"] is None


def test_explore_accepts_the_model_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`praxis explore` carries the same per-run --model override as regress."""
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.delenv("PRAXIS_BRAIN_MODEL", raising=False)
    captured: dict = {}

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return lambda prompt: {
            "candidate_observations": [], "new_risks": [],
            "new_uncertainties": [], "actions": 1, "tokens": 10,
            "visited_urls": [],
        }

    monkeypatch.setattr(cli_mod, "make_claude_brain", fake_factory)
    rc = _run(["explore", "--goal", "login", "--model", "pin-from-flag"],
              tmp_path)
    assert rc == 0
    assert captured.get("model") == "pin-from-flag"


def _seed_authed_yaml(*, being_tested: bool = False) -> str:
    """A seed for an authenticated goal: `auth_state.authenticated: true,
    scope: user`. `being_tested` makes authentication the SUBJECT under test."""
    bt = "true" if being_tested else "false"
    return f"""\
schema_version: "0"
goal_id: dashboard
goal: a logged-in user reaches the dashboard
target:
  app: testapp
  environment: local
auth_state:
  authenticated: true
  scope: user
  being_tested: {bt}
success_signals:
  - type: behavioral
    value: the dashboard renders for the authenticated user
    confidence: 1.0
    status: believed
    provenance:
      source_type: human
      source_id: pablo
      last_verified: "2026-06-07T00:00:00Z"
      observation_count: 1
meta:
  created_at: "2026-06-07T00:00:00Z"
  updated_at: "2026-06-07T00:00:00Z"
"""


def _fake_brain_factory(captured: dict):
    """A make_claude_brain replacement that captures kwargs and returns a brain
    which records the auth_state `session_for_goal` resolves to for the goal."""
    def factory(**kwargs):
        captured.update(kwargs)

        def brain(prompt: str) -> dict:
            sfg = kwargs.get("session_for_goal")
            captured["resolved_auth_state"] = sfg() if sfg is not None else None
            return {
                "observations": [{
                    "kind": "success", "type": "behavioral",
                    "value": "the dashboard renders for the authenticated user",
                    "source_type": "agent", "source_id": "praxis-cli",
                }],
                "actions": 1, "tokens": 10, "authenticated": True,
            }
        return brain
    return factory


def test_regress_wires_session_for_goal_with_the_goals_auth_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console regress brain gets a `session_for_goal` that resolves to the
    goal's `auth_state`, so the claude -p brain can reuse the saved session for an
    authenticated precondition goal (ADR-0026, ADR-0027 decision 2)."""
    _run(["init"], tmp_path)
    seed = tmp_path / "dashboard.yaml"
    seed.write_text(_seed_authed_yaml())
    _run(["learn", "dashboard", "--from-file", str(seed)], tmp_path)

    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    captured: dict = {}
    monkeypatch.setattr(cli_mod, "make_claude_brain", _fake_brain_factory(captured))

    rc = _run(["regress", "--goal", "dashboard"], tmp_path)
    assert rc == 0
    # session_for_goal was passed and resolved to this goal's auth_state.
    assert "session_for_goal" in captured
    resolved = captured["resolved_auth_state"]
    assert resolved is not None
    assert resolved.authenticated is True
    assert resolved.scope == "user"
    assert resolved.being_tested is False


def test_regress_missing_session_surfaces_auth_expired_not_a_false_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authenticated precondition goal whose saved session is MISSING (no env
    var, no `.praxis.auth/<role>.json`) is reported AUTH-EXPIRED naming the role,
    never a false REGRESSED and never a silent green (ADR-0026 decision 5). The
    real claude -p brain drives this: no `--from-file`, no real claude needed
    because the brain short-circuits WITHOUT driving the browser when the session
    is missing, so subprocess is never reached."""
    _run(["init"], tmp_path)
    seed = tmp_path / "dashboard.yaml"
    seed.write_text(_seed_authed_yaml())
    _run(["learn", "dashboard", "--from-file", str(seed)], tmp_path)

    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    # `claude` appears available so the real claude -p brain is selected; the
    # brain never shells out because the missing session short-circuits first.
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    capsys.readouterr()
    # Default-all aggregate run (the documented CI contract): the missing session
    # routes to AUTH-EXPIRED through classify_goal.
    rc = _run(["regress"], tmp_path)
    out = capsys.readouterr().out
    # AUTH-EXPIRED is a loud non-OK that fails the run (exit 1), not a false RED
    # and not a green.
    assert rc == 1
    assert "AUTH-EXPIRED" in out or "auth-expired" in out.lower()
    assert "user" in out  # the expired role is named


def test_regress_single_goal_missing_session_is_loud_not_a_false_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`praxis regress --goal <g>` on an authenticated goal with a missing
    session is AUTH-EXPIRED, not a silent UNCERTAIN green: the single-goal path
    re-classifies through `classify_goal` and fails the run (ADR-0026 dec. 5)."""
    _run(["init"], tmp_path)
    seed = tmp_path / "dashboard.yaml"
    seed.write_text(_seed_authed_yaml())
    _run(["learn", "dashboard", "--from-file", str(seed)], tmp_path)

    import sys
    cli_mod = sys.modules["praxis.cli.main"]
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    capsys.readouterr()
    rc = _run(["regress", "--goal", "dashboard"], tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "AUTH-EXPIRED" in err
    assert "user" in err  # the expired role is named


# --- explore --------------------------------------------------------------


def test_explore_requires_goal(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    rc = _run(["explore"], tmp_path)
    assert rc == 2  # missing --goal


def test_explore_reports_off_path_fraction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    obs_file = tmp_path / "agent.json"
    obs_file.write_text(json.dumps({
        "candidate_observations": [],
        "new_risks": [],
        "new_uncertainties": [],
        "actions": 3, "tokens": 800,
        "visited_urls": ["/login", "/admin", "/admin/users"],
    }))
    capsys.readouterr()
    rc = _run(["explore", "--goal", "login",
                "--happy-path", "/login", "/session",
                "--from-file", str(obs_file)], tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "off_path_fraction" in out
    # 2 of 3 visited URLs are off the happy path (/admin and /admin/users).
    assert "0.67" in out


# --- review --------------------------------------------------------------


def test_review_says_nothing_contested_when_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    capsys.readouterr()
    rc = _run(["review"], tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Nothing to review" in out or "nothing contested" in out.lower()


def test_review_surfaces_candidate_risks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A persisted candidate risk with a single source shows up in the
    Phase-2 `praxis review` queue with provenance + the promotion hint
    (ADR-0014 sec 4)."""
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)

    # Drive the explore path to persist a candidate risk with a single
    # source so the projection keeps it `contested`.
    obs_file = tmp_path / "agent.json"
    obs_file.write_text(json.dumps({
        "candidate_observations": [],
        "new_risks": [{
            "id": "phishy-redirect",
            "description": "login redirects to an unexpected host",
            "trigger": {"kind": "http", "method": "GET",
                         "path": "/login/callback",
                         "expect": "Location header matches the configured origin"},
            "status": "contested", "confidence": 0.6,
            "provenance": {
                "source_type": "agent", "source_id": "praxis-cli",
                "last_verified": "2026-06-07T00:00:00Z",
                "observation_count": 1,
            },
        }],
        "new_uncertainties": [],
        "actions": 1, "tokens": 200, "visited_urls": [],
    }))
    _run(["explore", "--goal", "login", "--from-file", str(obs_file)], tmp_path)

    capsys.readouterr()
    rc = _run(["review"], tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "phishy-redirect" in out
    assert "candidate_risk" in out
    # The promotion hint is rendered (seed event = new yaml seed).
    assert "seed" in out.lower()


# --- ADR-0035: environment selection (--env flag > PRAXIS_ENV env > ---------
# --- config.yaml default_env > single-entry auto-select; loud errors) -------


_TWO_ENVS = {
    "dev2": {"base_url": "https://dev2.example.com",
             "observed_app_version": "2.6.0"},
    "prod": {"base_url": "https://example.com"},
}

_ENVIRONMENTS_YAML = """\

environments:
  dev2:
    base_url: https://dev2.example.com
    observed_app_version: 2.6.0
  prod:
    base_url: https://example.com
default_env: dev2
"""


def _declare_envs(tmp_path: Path, extra: str = "") -> None:
    """Append the committed two-env map (ADR-0035 decision 1) to an inited
    project's config.yaml."""
    cfg = tmp_path / ".praxis" / "config.yaml"
    cfg.write_text(cfg.read_text() + _ENVIRONMENTS_YAML + extra)


def _pass_obs_file(tmp_path: Path) -> Path:
    """An agent observation file that matches the login seed (PASS)."""
    obs_file = tmp_path / "agent.json"
    obs_file.write_text(json.dumps({
        "observations": [{
            "kind": "success", "type": "behavioral",
            "value": "a Sign out control is present after submitting valid credentials",
            "source_type": "agent", "source_id": "praxis-cli",
        }],
        "actions": 5, "tokens": 1000, "visited_urls": [],
    }))
    return obs_file


def test_resolve_env_precedence_chain() -> None:
    """The resolution is a pure function in the CLI layer: flag > PRAXIS_ENV >
    default_env > single-entry auto-select, each level alone pins, and an
    empty string at any level counts as unset (ADR-0035 decision 2)."""
    from praxis.cli.main import PRAXIS_ENV_VAR, _resolve_env

    environ = {PRAXIS_ENV_VAR: "prod"}
    assert _resolve_env("dev2", _TWO_ENVS, "prod", environ=environ) == (
        "dev2", "--env flag")
    assert _resolve_env(None, _TWO_ENVS, "dev2", environ=environ) == (
        "prod", f"{PRAXIS_ENV_VAR} env")
    assert _resolve_env(None, _TWO_ENVS, "dev2", environ={}) == (
        "dev2", "config.yaml default_env")
    single = {"dev2": {"base_url": "https://dev2.example.com"}}
    assert _resolve_env(None, single, None, environ={}) == (
        "dev2", "single declared environment")
    # Empty values are unset at every level: blank flag / env var / default
    # never mask the level below, so a single-entry map still auto-selects.
    assert _resolve_env("", single, "", environ={PRAXIS_ENV_VAR: ""}) == (
        "dev2", "single declared environment")
    # Undeclared project, nothing set: today's behavior exactly.
    assert _resolve_env(None, None, None, environ={}) == (None, None)


def test_resolve_env_unknown_flag_name_is_loud() -> None:
    from praxis.cli.main import _resolve_env

    with pytest.raises(SystemExit) as exc:
        _resolve_env("staging", _TWO_ENVS, None, environ={})
    msg = str(exc.value)
    assert "staging" in msg and "dev2" in msg and "prod" in msg


def test_resolve_env_unknown_env_var_is_loud() -> None:
    from praxis.cli.main import PRAXIS_ENV_VAR, _resolve_env

    with pytest.raises(SystemExit) as exc:
        _resolve_env(None, _TWO_ENVS, None, environ={PRAXIS_ENV_VAR: "staging"})
    msg = str(exc.value)
    assert "staging" in msg and "dev2" in msg and "prod" in msg
    assert PRAXIS_ENV_VAR in msg


def test_resolve_env_unknown_default_env_is_loud() -> None:
    """A committed default_env typo must not silently fall through to the
    auto-select level: it errors naming the declared environments."""
    from praxis.cli.main import _resolve_env

    with pytest.raises(SystemExit) as exc:
        _resolve_env(None, _TWO_ENVS, "staging", environ={})
    msg = str(exc.value)
    assert "default_env" in msg and "staging" in msg
    assert "dev2" in msg and "prod" in msg


def test_resolve_env_unresolvable_multi_entry_is_loud() -> None:
    """A declared multi-entry map with no flag, no env var, and no default is
    a loud error naming the declared envs and the three ways to pick one."""
    from praxis.cli.main import PRAXIS_ENV_VAR, _resolve_env

    with pytest.raises(SystemExit) as exc:
        _resolve_env(None, _TWO_ENVS, None, environ={})
    msg = str(exc.value)
    assert "dev2" in msg and "prod" in msg
    assert "--env" in msg and PRAXIS_ENV_VAR in msg and "default_env" in msg


def test_resolve_env_flag_on_undeclared_project_is_loud() -> None:
    """`--env` on a project with NO environments map is a hard error: the user
    explicitly asked for something the config cannot honor."""
    from praxis.cli.main import _resolve_env

    with pytest.raises(SystemExit) as exc:
        _resolve_env("dev2", None, None, environ={})
    msg = str(exc.value)
    assert "dev2" in msg and "environments" in msg


def test_resolve_env_praxis_env_on_undeclared_warns_and_ignores(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PRAXIS_ENV on an undeclared project is ignored with a one-line stderr
    notice (never an error), so a pipeline-wide export cannot break repos that
    have not adopted environments (ADR-0035 decision 2)."""
    from praxis.cli.main import PRAXIS_ENV_VAR, _resolve_env

    got = _resolve_env(None, None, None, environ={PRAXIS_ENV_VAR: "dev2"})
    assert got == (None, None)
    err = capsys.readouterr().err
    assert PRAXIS_ENV_VAR in err and "ignor" in err


def test_project_context_env_aware_properties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an environment selected, base_url / environment /
    observed_app_version come from the selected entry of the declared map and
    the shadowed top-level keys are ignored; before selection (and on
    undeclared projects) the top-level keys read exactly as today."""
    from praxis.cli.main import ProjectContext

    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init", "--app", "tests", "--env", "local"], tmp_path)
    _declare_envs(tmp_path, "legacy_env: prod\nobserved_app_version: 9.9.9\n")

    proj = ProjectContext(tmp_path)
    # Parsing only: the config map + default + legacy_env are readable.
    envs = proj.environments
    assert envs is not None and set(envs) == {"dev2", "prod"}
    assert proj.default_env == "dev2"
    assert proj.legacy_env == "prod"
    # Before selection the legacy top-level reads apply.
    assert proj.environment == "local"
    assert proj.observed_app_version == "9.9.9"

    name, source = proj.select_environment("prod")
    assert (name, source) == ("prod", "--env flag")
    assert proj.environment == "prod"
    assert proj.base_url == "https://example.com"
    # prod declares no observed_app_version; the top-level 9.9.9 is shadowed.
    assert proj.observed_app_version is None

    proj2 = ProjectContext(tmp_path)
    assert proj2.select_environment(None) == ("dev2", "config.yaml default_env")
    assert proj2.base_url == "https://dev2.example.com"
    assert proj2.observed_app_version == "2.6.0"


def test_project_context_rejects_malformed_environments_map(
    tmp_path: Path,
) -> None:
    """A declared entry without a base_url fails loudly at read time instead
    of silently flipping the project back to single-env behavior."""
    from praxis.cli.main import ProjectContext

    _run(["init"], tmp_path)
    cfg = tmp_path / ".praxis" / "config.yaml"
    cfg.write_text(cfg.read_text() + "\nenvironments:\n  dev2:\n    app: x\n")
    with pytest.raises(SystemExit) as exc:
        _ = ProjectContext(tmp_path).environments
    assert "base_url" in str(exc.value)


def test_regress_env_flag_resolves_and_banners(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a declared project, `praxis regress --env prod` resolves the env and
    prints the one-line stderr banner naming the winning source (the ADR-0034
    banner posture); the verdict contract is unchanged."""
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    _declare_envs(tmp_path)
    obs_file = _pass_obs_file(tmp_path)
    capsys.readouterr()
    rc = _run(["regress", "--goal", "login", "--from-file", str(obs_file),
                "--env", "prod"], tmp_path)
    err = capsys.readouterr().err
    assert rc == 0
    assert "environment: prod (from --env flag)" in err


def test_regress_praxis_env_var_selects_when_no_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRAXIS_ENV (the CI channel) wins over the committed default_env."""
    monkeypatch.setenv("PRAXIS_ENV", "prod")
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    _declare_envs(tmp_path)
    obs_file = _pass_obs_file(tmp_path)
    capsys.readouterr()
    rc = _run(["regress", "--goal", "login", "--from-file", str(obs_file)],
              tmp_path)
    err = capsys.readouterr().err
    assert rc == 0
    assert "environment: prod (from PRAXIS_ENV env)" in err


def test_regress_unknown_env_flag_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    _declare_envs(tmp_path)
    obs_file = _pass_obs_file(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(["regress", "--goal", "login", "--from-file", str(obs_file),
               "--env", "staging"], tmp_path)
    msg = str(exc.value)
    assert "staging" in msg and "dev2" in msg and "prod" in msg


def test_regress_env_flag_on_undeclared_project_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    obs_file = _pass_obs_file(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(["regress", "--goal", "login", "--from-file", str(obs_file),
               "--env", "dev2"], tmp_path)
    assert "environments" in str(exc.value)


def test_explore_accepts_the_env_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`praxis explore` carries the same per-run --env selection as regress."""
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    _declare_envs(tmp_path)
    obs_file = tmp_path / "agent.json"
    obs_file.write_text(json.dumps({
        "candidate_observations": [], "new_risks": [],
        "new_uncertainties": [], "actions": 1, "tokens": 100,
        "visited_urls": [],
    }))
    capsys.readouterr()
    rc = _run(["explore", "--goal", "login", "--from-file", str(obs_file),
                "--env", "prod"], tmp_path)
    err = capsys.readouterr().err
    assert rc == 0
    assert "environment: prod (from --env flag)" in err


def test_status_declared_project_shows_map_default_and_banner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`praxis status` on a declared project lists the whole environments map
    with the default marked, reports the SELECTED env's base_url on the env
    line, and prints the resolved-env stderr banner."""
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    _declare_envs(tmp_path)
    capsys.readouterr()
    rc = _run(["status"], tmp_path)
    captured = capsys.readouterr()
    assert rc == 0
    # The default (dev2) resolved; its base_url is on the summary line.
    assert "env=dev2" in captured.out
    assert "base_url=https://dev2.example.com" in captured.out
    # The whole map is listed, default marked.
    assert "environments:" in captured.out
    assert "dev2: https://dev2.example.com" in captured.out
    assert "(default)" in captured.out
    assert "prod: https://example.com" in captured.out
    assert "environment: dev2 (from config.yaml default_env)" in captured.err
    # --env overrides the reported deployment.
    rc = _run(["status", "--env", "prod"], tmp_path)
    captured = capsys.readouterr()
    assert rc == 0
    assert "env=prod" in captured.out
    assert "base_url=https://example.com" in captured.out
    assert "environment: prod (from --env flag)" in captured.err


def test_status_unresolvable_multi_env_lists_map_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`praxis status` on a declared multi-entry map with NO resolution path
    (no --env, no PRAXIS_ENV, no default_env) is BEST-EFFORT, unlike
    regress/explore: status is the read-only discovery command a user runs
    precisely to SEE what environments exist, so it lists the map with no
    selected env, no banner, and exits 0."""
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    cfg = tmp_path / ".praxis" / "config.yaml"
    cfg.write_text(cfg.read_text() + (
        "\nenvironments:\n"
        "  dev2:\n    base_url: https://dev2.example.com\n"
        "  prod:\n    base_url: https://example.com\n"
    ))
    capsys.readouterr()
    rc = _run(["status"], tmp_path)
    captured = capsys.readouterr()
    assert rc == 0
    # No selected env on the summary line, the whole map listed, no default
    # marker (none is declared), no resolved-env stderr banner.
    assert "env=-" in captured.out
    assert "environments:" in captured.out
    assert "dev2: https://dev2.example.com" in captured.out
    assert "prod: https://example.com" in captured.out
    assert "(default)" not in captured.out
    assert "environment:" not in captured.err


def test_status_unknown_env_flag_still_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort status does NOT soften the explicit mistakes: an unknown
    `--env` name errors naming the declared environments, and `--env` on an
    undeclared project stays the hard error."""
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    # --env on an undeclared project: hard error, as on regress/explore.
    with pytest.raises(SystemExit) as exc:
        _run(["status", "--env", "dev2"], tmp_path)
    assert "environments" in str(exc.value)
    # Unknown name against a declared map: loud, naming the declared envs.
    _declare_envs(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(["status", "--env", "staging"], tmp_path)
    msg = str(exc.value)
    assert "staging" in msg and "dev2" in msg and "prod" in msg


def test_undeclared_project_regress_is_unchanged_no_banner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADR-0035 backward-compat bar, pinned: an undeclared project's
    `regress --from-file` keeps identical paths (pure-timestamp run dirs, same
    report locations), prints NO environment banner, and keeps the exit-code
    contract on both the pass and the fail path."""
    monkeypatch.delenv("PRAXIS_ENV", raising=False)
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    obs_file = _pass_obs_file(tmp_path)
    capsys.readouterr()
    rc = _run(["regress", "--goal", "login", "--from-file", str(obs_file)],
              tmp_path)
    captured = capsys.readouterr()
    assert rc == 0  # PASS exit code unchanged
    assert "environment:" not in captured.err  # no banner
    # Paths unchanged: runs/<timestamp>/ with no env suffix, reports in place.
    runs = tmp_path / ".praxis" / "runs"
    run_dirs = [p for p in runs.iterdir() if p.is_dir()]
    assert run_dirs and all("__" not in p.name for p in run_dirs)
    assert sorted(runs.glob("*/last-regress.md"))
    assert sorted(runs.glob("*/last-regress.xml"))
    # The fail path's exit code is unchanged too.
    obs_file.write_text(json.dumps({
        "observations": [{
            "kind": "failure", "type": "text",
            "value": "an invalid credentials banner appears",
            "source_type": "agent", "source_id": "praxis-cli",
        }],
        "actions": 5, "tokens": 1000, "visited_urls": [],
    }))
    rc = _run(["regress", "--goal", "login", "--from-file", str(obs_file)],
              tmp_path)
    captured = capsys.readouterr()
    assert rc == 1
    assert "environment:" not in captured.err


def test_undeclared_project_regress_ignores_praxis_env_with_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pipeline-wide `export PRAXIS_ENV=...` does not break an undeclared
    project: the run proceeds with today's behavior, a one-line stderr notice
    flags the ignored variable, and no environment banner is printed."""
    monkeypatch.setenv("PRAXIS_ENV", "dev2")
    _run(["init"], tmp_path)
    seed = tmp_path / "login.yaml"
    seed.write_text(_seed_login_yaml())
    _run(["learn", "login", "--from-file", str(seed)], tmp_path)
    obs_file = _pass_obs_file(tmp_path)
    capsys.readouterr()
    rc = _run(["regress", "--goal", "login", "--from-file", str(obs_file)],
              tmp_path)
    err = capsys.readouterr().err
    assert rc == 0  # exit code unchanged
    assert "PRAXIS_ENV" in err and "ignor" in err  # the notice
    assert "(from" not in err  # no resolved-env banner
