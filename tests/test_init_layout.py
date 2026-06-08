"""`praxis init` materializes the ADR-0021 directory convention.

These tests pin the exact on-disk shape `praxis init` produces and the two
load-bearing safety properties of the init operation:

    - the committed tree is config + knowledge + candidates + .praxisignore, and
      the per-machine event log lives under the gitignored runs/<timestamp>/
      (ADR-0021 decision 1 and 2);
    - the repo-root .gitignore carries BOTH `.praxis/runs/` and `.praxis.secrets`
      exactly once, even after a second init (ADR-0021 decisions 5 and 6), so
      the secrets file can never be committed by accident and is gitignored
      BEFORE any secret could be written;
    - the Claude Code skills ship as package data and `praxis init` scaffolds
      them into `.claude/skills/` (the novel skill-in-wheel round trip).
"""
from __future__ import annotations

import os
from pathlib import Path

from praxis.cli import main


def _run(args: list[str], cwd: Path) -> int:
    old = Path.cwd()
    os.chdir(cwd)
    try:
        return main(args)
    finally:
        os.chdir(old)


# --- the exact ADR-0021 tree ----------------------------------------------


def test_init_produces_exact_adr0021_tree(tmp_path: Path) -> None:
    rc = _run(["init", "--app", "demo"], tmp_path)
    assert rc == 0
    pdir = tmp_path / ".praxis"

    # Committed set.
    assert (pdir / "config.yaml").is_file()
    assert (pdir / "knowledge").is_dir()
    assert (pdir / "candidates").is_dir()
    assert (pdir / ".praxisignore").is_file()
    # Gitignored, local, regenerable per-machine log dir.
    assert (pdir / "runs").is_dir()

    # The old Phase-1 layout (events/, reports/, a .praxis/.gitignore) is gone:
    # runs/ replaces events/ + reports/, and the ignore lines live in the
    # repo-root .gitignore, not inside .praxis/.
    assert not (pdir / "events").exists()
    assert not (pdir / "reports").exists()
    assert not (pdir / ".gitignore").exists()


def test_knowledge_and_candidates_start_empty(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    pdir = tmp_path / ".praxis"
    assert list((pdir / "knowledge").iterdir()) == []
    assert list((pdir / "candidates").iterdir()) == []


# --- gitignore: both lines, exactly once, idempotent ----------------------


def _gitignore_lines(repo_root: Path) -> list[str]:
    text = (repo_root / ".gitignore").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def test_gitignore_contains_both_lines_once(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    lines = _gitignore_lines(tmp_path)
    assert lines.count(".praxis/runs/") == 1
    assert lines.count(".praxis.secrets") == 1


def test_second_init_does_not_duplicate_ignore_lines(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    rc = _run(["init", "--force"], tmp_path)
    assert rc == 0
    lines = _gitignore_lines(tmp_path)
    assert lines.count(".praxis/runs/") == 1
    assert lines.count(".praxis.secrets") == 1


def test_init_appends_to_a_preexisting_gitignore(tmp_path: Path) -> None:
    # A project that already has a .gitignore must keep its lines and gain ours.
    (tmp_path / ".gitignore").write_text("node_modules/\n*.log\n", encoding="utf-8")
    _run(["init"], tmp_path)
    lines = _gitignore_lines(tmp_path)
    assert "node_modules/" in lines
    assert "*.log" in lines
    assert lines.count(".praxis/runs/") == 1
    assert lines.count(".praxis.secrets") == 1


def test_init_creates_gitignore_when_absent(tmp_path: Path) -> None:
    assert not (tmp_path / ".gitignore").exists()
    _run(["init"], tmp_path)
    assert (tmp_path / ".gitignore").is_file()
    lines = _gitignore_lines(tmp_path)
    assert ".praxis/runs/" in lines
    assert ".praxis.secrets" in lines


def test_secrets_gitignored_before_any_secret_written(tmp_path: Path) -> None:
    # The whole secret contract rests on .praxis.secrets being gitignored from
    # the moment init runs, so a credential dropped in next can never be
    # committed. After init the ignore line exists and the secrets file does NOT
    # (no secret has been written yet).
    _run(["init"], tmp_path)
    assert ".praxis.secrets" in _gitignore_lines(tmp_path)
    assert not (tmp_path / ".praxis.secrets").exists()


# --- skills scaffolded into .claude/skills/ -------------------------------


def test_skills_scaffolded_from_package_data(tmp_path: Path) -> None:
    _run(["init"], tmp_path)
    skills_dir = tmp_path / ".claude" / "skills"
    assert skills_dir.is_dir()
    skill_mds = list(skills_dir.rglob("SKILL.md"))
    assert skill_mds, "praxis init must scaffold at least one SKILL.md"
    # Each scaffolded SKILL.md is well formed (Claude Code frontmatter).
    for md in skill_mds:
        text = md.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "name:" in text
        assert "description:" in text


def test_scaffolded_skills_match_packaged_skills(tmp_path: Path) -> None:
    # The scaffold is a faithful copy of what the wheel ships: same relative
    # subpaths, same bytes (ADR-0021 decision 5 unpacks the ADR-0020 package
    # data unchanged).
    from praxis.resources import iter_skill_files, skills_root

    _run(["init"], tmp_path)
    skills_dir = tmp_path / ".claude" / "skills"
    src_root = Path(str(skills_root()))
    for src in iter_skill_files():
        rel = src.relative_to(src_root)
        dest = skills_dir / rel
        assert dest.is_file(), f"missing scaffolded skill file: {rel}"
        assert dest.read_bytes() == src.read_bytes()
