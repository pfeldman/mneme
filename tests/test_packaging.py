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

import os
from pathlib import Path

from praxis import resources
from praxis.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SCHEMA = REPO_ROOT / "schema" / "knowledge.schema.json"

# The regress / explore local-brain skills (ADR-0023 decision 5 + 8) ship as
# package data under these package-relative subpaths and scaffold to the same
# relative paths under `.claude/skills/`.
REGRESS_SKILL_REL = Path("praxis") / "regress" / "SKILL.md"
EXPLORE_SKILL_REL = Path("praxis") / "explore" / "SKILL.md"


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


# --- the regress / explore skills (Wave 2 Step 8, ADR-0023) ----------------


def _skills_by_rel() -> dict[Path, Path]:
    """Map each shipped skill's package-relative path to its concrete Path."""
    root = Path(str(resources.skills_root()))
    return {p.relative_to(root): p for p in resources.iter_skill_files()}


def _run_init(args: list[str], cwd: Path) -> int:
    old = Path.cwd()
    os.chdir(cwd)
    try:
        return main(args)
    finally:
        os.chdir(old)


def test_regress_and_explore_skills_resolve_from_package_data() -> None:
    # Both local-brain skills (ADR-0023 decision 5 + 8) must ship as package
    # data so the wheel carries them and `praxis init` has them to scaffold.
    by_rel = _skills_by_rel()
    assert REGRESS_SKILL_REL in by_rel, "praxis:regress skill is not shipped"
    assert EXPLORE_SKILL_REL in by_rel, "praxis:explore skill is not shipped"

    for rel in (REGRESS_SKILL_REL, EXPLORE_SKILL_REL):
        text = by_rel[rel].read_text(encoding="utf-8")
        # Claude Code skill format: name + description frontmatter.
        assert text.startswith("---"), f"{rel} is missing frontmatter"
        head = text.split("---", 2)
        assert len(head) >= 3, f"{rel} frontmatter block is not closed"
        assert "name:" in head[1], f"{rel} frontmatter missing name"
        assert "description:" in head[1], f"{rel} frontmatter missing description"


def test_regress_and_explore_skills_are_scaffolded_by_init(tmp_path: Path) -> None:
    rc = _run_init(["init", "--app", "demo"], tmp_path)
    assert rc == 0
    skills_dir = tmp_path / ".claude" / "skills"
    assert (skills_dir / REGRESS_SKILL_REL).is_file(), (
        "praxis init must scaffold the praxis:regress skill"
    )
    assert (skills_dir / EXPLORE_SKILL_REL).is_file(), (
        "praxis init must scaffold the praxis:explore skill"
    )
    # The scaffold is a faithful byte copy of what the wheel ships.
    by_rel = _skills_by_rel()
    for rel in (REGRESS_SKILL_REL, EXPLORE_SKILL_REL):
        assert (skills_dir / rel).read_bytes() == by_rel[rel].read_bytes()


def test_regress_skill_states_triage_is_advisory_and_never_mutates() -> None:
    # ADR-0023 decision 5: the regress skill triage is advisory and NEVER
    # mutates committed knowledge on its own; a STALE update is a human seed
    # event. The skill text must encode that contract so a local-brain run
    # cannot read it as license to auto-edit knowledge.
    text = _skills_by_rel()[REGRESS_SKILL_REL].read_text(encoding="utf-8").lower()
    assert "advisory" in text, "regress skill must state triage is advisory"
    assert "never mutate" in text or "never auto-mutate" in text, (
        "regress skill must state it never mutates committed knowledge"
    )
    # The break-vs-drift routing both verdicts produce.
    assert "regressed" in text and "stale" in text
    # STALE routes to a human seed event, not an automatic edit.
    assert "human seed event" in text
    assert "re-seed" in text


def test_explore_skill_states_triage_is_advisory_and_never_mutates() -> None:
    # ADR-0023 decision 8: explore triages fresh findings inline (promote /
    # leave / discard) applied as the matching review action, but a promote is
    # a human review action, never an automatic mutation of committed knowledge.
    text = _skills_by_rel()[EXPLORE_SKILL_REL].read_text(encoding="utf-8").lower()
    assert "advisory" in text, "explore skill must state triage is advisory"
    assert "never auto-mutate" in text or "never mutate" in text, (
        "explore skill must state it never auto-mutates committed knowledge"
    )
    # The three inline triage actions (ADR-0023 decision 8).
    assert "promote" in text and "leave" in text and "discard" in text
    # The aggregate contested queue stays `praxis review`.
    assert "praxis review" in text
