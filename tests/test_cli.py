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
    assert (pdir / "config.yaml").exists()
    assert (pdir / "knowledge").is_dir()
    assert (pdir / "events").is_dir()
    assert (pdir / "reports").is_dir()
    assert (pdir / ".gitignore").exists()
    out = capsys.readouterr().out
    assert "initialized praxis project" in out


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    rc = _run(["init"], tmp_path)
    assert rc == 2


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
    md = tmp_path / ".praxis" / "reports" / "last-regress.md"
    xml = tmp_path / ".praxis" / "reports" / "last-regress.xml"
    assert md.exists() and xml.exists()
    assert "**pass**" in md.read_text()


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
    md = (tmp_path / ".praxis" / "reports" / "last-regress.md").read_text()
    assert "**fail**" in md
    assert "regression" in md.lower()


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
