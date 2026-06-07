"""Tests for the Phase-2 real-app port (Conduit) under
`experiments/regression_recall_real/`.

These tests exercise the manifest loader, the goal slate shape, the
seeded knowledge YAMLs (schema + model agreement), and that `auth_state`
is present on every goal's seed. The slow Conduit bring-up gate lives in
`tests/test_conduit_bringup.py` (skipped by default).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from praxis.model import AuthState, KnowledgeFile, load, validate_against_json_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = REPO_ROOT / "experiments" / "regression_recall_real"
KNOWLEDGE_DIR = EXPERIMENT_DIR / "knowledge"

EXPECTED_GOALS = (
    "login",
    "publish_article",
    "favorite_article",
    "follow_user",
    "edit_article",
)


def test_manifest_loads_with_expected_goal_slate() -> None:
    """ADR-0016 sec 4: the goal slate is sealed at five Conduit goals
    parallel-but-distinct from Phase 1's four. The brief says 4 or 5; we
    pre-register at 5 so a future contraction to 4 is a recorded change."""
    from regression_recall_real.manifest import default_manifest

    m = default_manifest()
    assert m.sut.name == "conduit"
    assert m.sut.selection_adr == "0016"
    assert m.schema_extension_adr == "0017"
    assert m.goal_ids() == EXPECTED_GOALS


def test_manifest_regressions_target_known_goals() -> None:
    """The manifest loader rejects regressions pointing at goals not in the
    slate; this test just confirms a clean manifest loads (negative cases are
    covered by the loader's validation)."""
    from regression_recall_real.manifest import default_manifest

    m = default_manifest()
    assert len(m.regressions) >= 5
    goal_set = set(m.goal_ids())
    for r in m.regressions:
        assert r.goal_id in goal_set


def test_manifest_rejects_regression_with_unknown_goal(tmp_path) -> None:
    """Loader-level guard: a manifest with a regression pointing at a goal not
    in the slate is rejected loud (mirrors ADR-0009 sealed-manifest discipline)."""
    import json

    from regression_recall_real.manifest import default_manifest, load_manifest

    raw = json.loads((EXPERIMENT_DIR / "manifest.json").read_text())
    raw["regressions"][0]["goal_id"] = "no_such_goal"
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="unknown goal"):
        load_manifest(bad)
    # The sealed default still loads cleanly.
    assert default_manifest().sut.name == "conduit"


@pytest.mark.parametrize("goal_id", EXPECTED_GOALS)
def test_seeded_knowledge_file_validates_against_active_schema(goal_id: str) -> None:
    """Each seeded knowledge YAML for a Conduit goal validates against the
    active Phase-1 schema PLUS the Phase-2 additive `auth_state` field. The
    same test pattern as `tests/test_schema_examples_validate.py` for the
    reference examples."""
    path = KNOWLEDGE_DIR / f"{goal_id}.knowledge.yaml"
    kf = load(path)
    assert isinstance(kf, KnowledgeFile)
    # JSON-schema round-trip catches drift between model and schema.
    validate_against_json_schema(kf.model_dump(mode="json", exclude_none=True))


@pytest.mark.parametrize("goal_id", EXPECTED_GOALS)
def test_seeded_knowledge_carries_auth_state(goal_id: str) -> None:
    """ADR-0017 sec 1: the auth_state projection is what makes the Phase-2 port
    honest. Every Conduit goal's seed declares the post-success posture so
    R-mode and E-mode share it (the cross-goal-reuse point of the field)."""
    path = KNOWLEDGE_DIR / f"{goal_id}.knowledge.yaml"
    kf = load(path)
    assert kf.auth_state is not None, (
        f"goal {goal_id} seed missing auth_state; ADR-0017 sec 1 requires it "
        f"for goals that probe a logged-in surface."
    )
    assert isinstance(kf.auth_state, AuthState)
    assert kf.auth_state.authenticated is True
    # Phase-2 Conduit goals are all `user`-scope (no admin surface in the slate).
    assert kf.auth_state.scope == "user"


def test_seeded_knowledge_does_not_leak_credentials() -> None:
    """ADR-0017 sec 2: the seed authors are explicitly forbidden from
    embedding tokens / cookies / user_ids / session_ids / JWTs / emails in
    seeded knowledge. This test scans every seeded YAML text for those
    field-name tokens; the model validator would catch them at load too, but
    a textual scan also flags an `expect:` value or a `value:` field that
    mentions one literally (vs. talking about it semantically)."""
    forbidden_substrings = (
        "Bearer eyJ",
        "Set-Cookie:",
        "Cookie:",
        "user_id=",
        "session_id=",
        "sid=",
        "JWT_SECRET=",
        "@example.com",
        "@example.org",
        "tenant_id=",
        "org_id=",
        "workspace_id=",
    )
    for goal_id in EXPECTED_GOALS:
        path = KNOWLEDGE_DIR / f"{goal_id}.knowledge.yaml"
        text = path.read_text()
        for needle in forbidden_substrings:
            assert needle not in text, (
                f"seeded YAML {goal_id}.knowledge.yaml contains forbidden "
                f"substring {needle!r}; ADR-0017 sec 2 rejects credentials "
                f"and per-user/per-session identifiers as durable knowledge."
            )


def test_bringup_script_present_and_executable() -> None:
    """ADR-0016 sec 1 C1: bring-up via a single script is part of the SUT
    selection criteria. The script must exist on disk and be marked
    executable so a stranger can run it without chmod surgery."""
    script = EXPERIMENT_DIR / "setup" / "bring_up.sh"
    compose = EXPERIMENT_DIR / "setup" / "docker-compose.yml"
    assert script.exists(), "missing bring_up.sh"
    assert compose.exists(), "missing docker-compose.yml"
    import os
    import stat

    mode = os.stat(script).st_mode
    assert mode & stat.S_IXUSR, "bring_up.sh is not executable for the owner"


def test_bringup_script_supports_check_and_teardown_subcommands() -> None:
    """The bring-up script must offer --check (idempotent probe) and
    --teardown (cleanup), so CI / Pablo can call it without leaving
    orphaned containers."""
    script = EXPERIMENT_DIR / "setup" / "bring_up.sh"
    text = script.read_text()
    assert "--check" in text
    assert "--teardown" in text
    # The 30-minute ceiling (ADR-0016 C1) is the default; the env var
    # override lets CI tighten it without editing the script.
    assert "PRAXIS_CONDUIT_DEADLINE_SECONDS" in text
    assert "1800" in text  # 30 * 60 default
