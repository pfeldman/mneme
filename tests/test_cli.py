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
