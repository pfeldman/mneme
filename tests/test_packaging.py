"""Packaging accessors resolve at runtime (ADR-0020 decisions 6 and 7).

These tests pin the two pieces of package data the wheel must ship and that
`praxis.resources` must resolve both from an installed wheel and from the src
tree:

    - the JSON knowledge schema, byte-identical to the repo source of truth, so
      an installed Praxis validates against the SAME schema the repo tests;
    - the Claude Code skills tree, non-empty, so `praxis init` has something to
      scaffold into `.claude/skills/`.
"""
from __future__ import annotations

from pathlib import Path

from praxis import resources

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SCHEMA = REPO_ROOT / "schema" / "knowledge.schema.json"


def test_packaged_schema_resolves_and_is_byte_identical() -> None:
    # The repo schema is the single source of truth; the packaged accessor must
    # return exactly those bytes whether running from the wheel or from src.
    expected = REPO_SCHEMA.read_bytes()
    assert resources.schema_bytes() == expected
    assert resources.schema_text() == expected.decode("utf-8")


def test_packaged_schema_path_points_at_the_schema() -> None:
    path = resources.schema_path()
    assert path.is_file()
    assert path.name == resources.SCHEMA_RESOURCE_NAME
    assert path.read_bytes() == REPO_SCHEMA.read_bytes()


def test_packaged_skills_dir_resolves_and_is_non_empty() -> None:
    root = resources.skills_root()
    # The skills tree must resolve as a real directory and carry at least one
    # SKILL.md so the ship-then-scaffold path has something to copy.
    root_path = Path(str(root))
    assert root_path.is_dir()

    skill_files = resources.iter_skill_files()
    assert skill_files, "packaged skills tree must ship at least one skill file"
    assert any(p.name == "SKILL.md" for p in skill_files)


def test_skill_files_carry_name_and_description_frontmatter() -> None:
    # Every shipped SKILL.md must be in Claude Code skill format (name +
    # description frontmatter) so the scaffolded skill is well formed.
    skill_mds = [p for p in resources.iter_skill_files() if p.name == "SKILL.md"]
    assert skill_mds, "expected at least one SKILL.md among the shipped skills"
    for md in skill_mds:
        text = md.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{md} is missing frontmatter"
        head = text.split("---", 2)
        assert len(head) >= 3, f"{md} frontmatter block is not closed"
        front = head[1]
        assert "name:" in front, f"{md} frontmatter missing name"
        assert "description:" in front, f"{md} frontmatter missing description"
