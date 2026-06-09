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
# relative paths under `.claude/skills/`. The tree is FLAT (one dir per skill,
# named by the skill's frontmatter name) so Claude Code discovers each as a
# slash command: a nested `praxis/<skill>/` layout is not discovered.
REGRESS_SKILL_REL = Path("praxis-regress") / "SKILL.md"
EXPLORE_SKILL_REL = Path("praxis-explore") / "SKILL.md"

# The teach local-brain authoring skill (ADR-0022) ships at this package-relative
# subpath and scaffolds to the same relative path under `.claude/skills/`.
TEACH_SKILL_REL = Path("praxis-teach") / "SKILL.md"


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


def test_teach_skill_documents_structured_checks() -> None:
    # ADR-0031 teach-UX follow-up: the teach skill must instruct the authoring
    # agent to emit a typed `check` for relational / after-action facts instead
    # of prose, or a check can only reach a seed by hand-editing YAML (off the
    # normal authoring flow). The guidance must name both check kinds so it
    # cannot silently regress out.
    text = _skills_by_rel()[TEACH_SKILL_REL].read_text(encoding="utf-8")
    lower = text.lower()
    assert "list_count_delta" in text, "teach must document the count-delta check"
    assert "element_membership" in text, "teach must document the membership check"
    # The trigger: a relational / after-action fact, not a fixed phrase.
    assert "count delta" in lower or "count_delta" in lower
    assert "relation" in lower
    # The non-leak rule: the abstract slot is seeded, never the concrete id.
    assert "abstract" in lower and "never a concrete" in lower


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


# --- the teach skill (Wave 2 Step 10, ADR-0022) ----------------------------


def test_teach_skill_resolves_from_package_data() -> None:
    # The teach authoring skill (ADR-0022 decision 1) must ship as package data
    # so the wheel carries it and `praxis init` has it to scaffold.
    by_rel = _skills_by_rel()
    assert TEACH_SKILL_REL in by_rel, "praxis:teach skill is not shipped"

    text = by_rel[TEACH_SKILL_REL].read_text(encoding="utf-8")
    # Claude Code skill format: name + description frontmatter.
    assert text.startswith("---"), f"{TEACH_SKILL_REL} is missing frontmatter"
    head = text.split("---", 2)
    assert len(head) >= 3, f"{TEACH_SKILL_REL} frontmatter block is not closed"
    assert "name:" in head[1], f"{TEACH_SKILL_REL} frontmatter missing name"
    assert "description:" in head[1], (
        f"{TEACH_SKILL_REL} frontmatter missing description"
    )


def test_teach_skill_is_scaffolded_by_init(tmp_path: Path) -> None:
    rc = _run_init(["init", "--app", "demo"], tmp_path)
    assert rc == 0
    skills_dir = tmp_path / ".claude" / "skills"
    assert (skills_dir / TEACH_SKILL_REL).is_file(), (
        "praxis init must scaffold the praxis:teach skill"
    )
    # The scaffold is a faithful byte copy of what the wheel ships.
    by_rel = _skills_by_rel()
    assert (skills_dir / TEACH_SKILL_REL).read_bytes() == (
        by_rel[TEACH_SKILL_REL].read_bytes()
    )


def test_teach_skill_encodes_the_four_typed_prompt_types() -> None:
    # ADR-0022 decision 2: when blocked the agent asks EXACTLY ONE of four typed
    # questions, never an open-ended dump. The skill text must name all four
    # types so a local-brain run cannot collapse them into a free-text prompt.
    text = _skills_by_rel()[TEACH_SKILL_REL].read_text(encoding="utf-8").lower()
    for prompt_type in ("credential", "navigation-hint", "role", "confirmation"):
        assert prompt_type in text, (
            f"teach skill must name the {prompt_type!r} typed prompt"
        )
    # The protocol is one typed question at a time, never an open-ended dump.
    assert "exactly one" in text
    assert "open-ended" in text


def test_teach_skill_encodes_the_dual_end_condition() -> None:
    # ADR-0022 decision 3: a session ends successfully only when BOTH halves
    # hold - the happy path was observed AND the human confirmed - with a budget
    # plus wall-time backstop and a loud not-converged outcome. The skill text
    # must encode both halves and the backstop.
    text = _skills_by_rel()[TEACH_SKILL_REL].read_text(encoding="utf-8").lower()
    assert "dual end condition" in text
    assert "happy-path observed" in text or "happy path observed" in text
    assert "human-confirm" in text or "human confirm" in text
    # The backstop and the loud not-converged outcome (ADR-0022 decision 3).
    assert "backstop" in text
    assert "not converged" in text or "not-converged" in text
    assert "writes no goal" in text or "no goal" in text


def test_teach_skill_encodes_skill_only_and_credentials_never_persisted() -> None:
    # ADR-0019 section 5: teach is skill-only and human-in-the-loop; there is no
    # console command and no CI teach. ADR-0022 decisions 4-6: human-seeded
    # output, credentials never persisted, no silent overwrite of a believed
    # goal. The skill text must encode these contracts.
    text = _skills_by_rel()[TEACH_SKILL_REL].read_text(encoding="utf-8").lower()
    # Skill-only, always human-in-the-loop (ADR-0019 section 5).
    assert "human-in-the-loop" in text
    assert "no `praxis teach` console command" in text or (
        "no praxis teach console command" in text
    )
    assert "no ci teach" in text
    # Human-seeded output under ADR-0005 (source_type human).
    assert "source_type = human" in text or "source_type human" in text
    # Credentials drive the browser but are never persisted (decision 5).
    assert "never persist" in text or "never persisted" in text
    assert "auth_state" in text
    # No silent overwrite of a believed goal -> contested candidate (decision 6).
    assert "contested candidate" in text
    # Navigation hints are invariants, never selectors (decision 2).
    assert "never a" in text and ("selector" in text or "coordinate" in text)


# --- the ADR-0026 session-reuse protocol in the three skills ---------------


def test_teach_skill_saves_the_session_after_login() -> None:
    # ADR-0026 decisions 1 + 7: after the human login (2FA passed live, the code
    # never persisted), teach EXPORTS the browser storageState via the Playwright
    # MCP and SAVES it for the role through the seam. The skill text must encode
    # the save-after-login bootstrap and that the session is a secret (gitignored,
    # never committed, never knowledge).
    text = _skills_by_rel()[TEACH_SKILL_REL].read_text(encoding="utf-8").lower()
    assert "storagestate" in text, "teach skill must export the storageState"
    assert "playwright mcp" in text, "teach skill must name the Playwright MCP export"
    assert "save_session_for_role" in text, "teach skill must call the save seam"
    # The session is a secret: gitignored, never committed, never knowledge.
    assert ".praxis.auth" in text or "praxis_auth_state" in text
    assert "never committed" in text
    assert "never knowledge" in text or "never recorded into knowledge" in text
    # The 2FA code is never persisted (ADR-0022 decision 5 carries to the code).
    assert "2fa" in text and ("never persisted" in text or "never persist" in text)


def _assert_load_before_run_and_reauth(rel: Path) -> None:
    """Both regress and explore must LOAD the session before driving the browser
    and, on AUTH-EXPIRED, ASK the human to re-authenticate (pass 2FA once) and
    re-save, while console / CI fails loud naming AUTH-EXPIRED + the role, and the
    email-2FA-vs-TOTP refresh cost is referenced (ADR-0026 decisions 5, 7)."""
    # Collapse whitespace so a line-wrapped phrase still matches as one span.
    text = " ".join(_skills_by_rel()[rel].read_text(encoding="utf-8").lower().split())
    # LOAD the saved session BEFORE driving the browser (decisions 1, 3).
    assert "load_session_for_role" in text, f"{rel} must call the load seam"
    assert "before driving the browser" in text, (
        f"{rel} must load the session before driving the browser"
    )
    assert "inject it into the browser context" in text
    # On AUTH-EXPIRED the skill asks the human to re-authenticate and re-saves.
    assert "auth-expired" in text, f"{rel} must name the AUTH-EXPIRED outcome"
    assert "re-authenticate" in text
    assert "save_session_for_role" in text, (
        f"{rel} must re-save the refreshed session on AUTH-EXPIRED"
    )
    # The console / CI surface fails loud naming AUTH-EXPIRED + the role.
    assert "fails loudly" in text or "fail loudly" in text or "fails loud" in text
    assert "non-zero exit" in text
    assert "never a silent green" in text
    # The email-2FA (manual refresh) vs TOTP (CI self-refresh) cost reference.
    assert "totp" in text
    assert "email" in text


def test_regress_skill_loads_session_before_run_and_reauths_on_auth_expired() -> None:
    _assert_load_before_run_and_reauth(REGRESS_SKILL_REL)


def test_explore_skill_loads_session_before_run_and_reauths_on_auth_expired() -> None:
    _assert_load_before_run_and_reauth(EXPLORE_SKILL_REL)
