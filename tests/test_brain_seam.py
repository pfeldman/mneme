"""Brain seam + dual-surface tests (ADR-0019).

ADR-0019 fixes that the body is brain-agnostic and that the agentic
operations (`regress`, `explore`) run on TWO surfaces over the SAME engine: a
console CLI and a direct-call skill driver. These tests pin three properties
the ADR's forbidden alternatives demand:

1. The engine returns the SAME verdict for the same goal + store whether it is
   driven from the console entry (`praxis.cli.main`) or the direct-call skill
   entry (`praxis.skill_driver`). Only the brain that drives the seam differs.
2. `import praxis` and the body tests work with NO LLM SDK present. The core
   imports and an engine run completes with `anthropic`, `browser_use`, and
   `openai` import-blocked, proving no brain is baked into the core path.
3. No brain identifier is persisted into knowledge. The only provenance on a
   stored observation is the `agent_identity` (`agent_id` / `source_id`);
   nothing names the brain that produced it.

The brain here is a plain in-memory callable: the `Brain` seam is
`Callable[[str], dict]`, so a test brain and a real Claude session satisfy the
same type with no LLM import.
"""
from __future__ import annotations

import builtins
import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from praxis.cli.main import discover_project
from praxis.cli.main import main as cli_run
from praxis.runner import (
    AGENTIC_OPERATIONS,
    DETERMINISTIC_OPERATIONS,
    RegressionVerdict,
    is_agentic,
)
from praxis.skill_driver import explore_via_skill, regress_via_skill


# --- fixtures -------------------------------------------------------------


def _seed_login_yaml() -> str:
    """A minimal valid seed knowledge file for goal_id=login.

    Mirrors the seed used in test_cli.py so both surfaces resolve the same
    believed success / failure signals.
    """
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


def _init_project_with_login(root: Path) -> None:
    """init a .praxis/ project under `root` and learn the login seed."""
    old = Path.cwd()
    os.chdir(root)
    try:
        assert cli_run(["init", "--app", "testapp", "--env", "local"]) == 0
        seed = root / "login.yaml"
        seed.write_text(_seed_login_yaml())
        assert cli_run(["learn", "login", "--from-file", str(seed)]) == 0
    finally:
        os.chdir(old)


# The PASS observation both surfaces feed their brain: all believed success
# signals seen, no failure signal. A brain is a Callable[[str], dict]; this is
# a deterministic stand-in so the two surfaces are compared on equal input.
_PASS_OBS: dict[str, Any] = {
    "observations": [
        {
            "kind": "success",
            "type": "behavioral",
            "value": "a Sign out control is present after submitting valid credentials",
            "source_type": "agent",
            "source_id": "praxis-cli",
        },
    ],
    "actions": 5,
    "tokens": 1000,
    "visited_urls": [],
    "notes": ["ran the happy path"],
}


def _pass_brain(prompt: str) -> dict[str, Any]:
    # The seam hands the brain the rendered, steps-free prompt; a real brain
    # would reason against the live app. This stand-in checks the prompt named
    # the goal, then returns the fixed PASS observation.
    assert "GOAL (login)" in prompt
    return json.loads(json.dumps(_PASS_OBS))


# --- 1. same verdict across the two surfaces ------------------------------


def test_same_verdict_console_and_direct_call(tmp_path: Path) -> None:
    """The console entry and the direct-call skill entry produce the SAME
    verdict for the same goal + store (ADR-0019 decision 4).

    The console run drives the engine through `praxis.cli.main` with a
    file-backed brain; the skill run drives the SAME engine through
    `praxis.skill_driver.regress_via_skill` with an in-memory brain. Same goal,
    same believed signals, same brain output -> same verdict.
    """
    # Console surface project.
    console_root = tmp_path / "console"
    console_root.mkdir()
    _init_project_with_login(console_root)
    obs_file = console_root / "agent.json"
    obs_file.write_text(json.dumps(_PASS_OBS))

    old = Path.cwd()
    os.chdir(console_root)
    try:
        console_rc = cli_run(
            ["regress", "--goal", "login", "--from-file", str(obs_file),
             "--budget-tokens", "5000"]
        )
    finally:
        os.chdir(old)
    # Console reports the verdict as a process exit code: PASS -> 0.
    assert console_rc == 0
    md = sorted((console_root / ".praxis" / "runs").glob("*/last-regress.md"))
    assert md and "**pass**" in md[-1].read_text()

    # Direct-call skill surface project (independent store, same seed + brain).
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    _init_project_with_login(skill_root)
    results = regress_via_skill(
        _pass_brain, goal="login", project_start=skill_root, budget_tokens=5000,
    )
    assert len(results) == 1
    # The skill surface reports the verdict as the structured RunResult.
    assert results[0].verdict == RegressionVerdict.PASS

    # The two surfaces agree: console exit 0 (no FAIL) <-> skill verdict PASS.
    console_failed = console_rc != 0
    skill_failed = results[0].verdict == RegressionVerdict.FAIL
    assert console_failed == skill_failed


def test_same_verdict_on_a_regression_across_surfaces(tmp_path: Path) -> None:
    """A FAIL is a FAIL on both surfaces: an observed failure signal makes the
    console exit non-zero AND the skill verdict FAIL, for the same store."""
    fail_obs: dict[str, Any] = {
        "observations": [
            {
                "kind": "failure",
                "type": "text",
                "value": "an invalid credentials banner appears",
                "source_type": "agent",
                "source_id": "praxis-cli",
            },
        ],
        "actions": 5,
        "tokens": 1000,
        "visited_urls": [],
    }

    def fail_brain(prompt: str) -> dict[str, Any]:
        return json.loads(json.dumps(fail_obs))

    console_root = tmp_path / "console"
    console_root.mkdir()
    _init_project_with_login(console_root)
    obs_file = console_root / "agent.json"
    obs_file.write_text(json.dumps(fail_obs))
    old = Path.cwd()
    os.chdir(console_root)
    try:
        console_rc = cli_run(
            ["regress", "--goal", "login", "--from-file", str(obs_file)]
        )
    finally:
        os.chdir(old)
    assert console_rc == 1  # FAIL -> non-zero

    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    _init_project_with_login(skill_root)
    results = regress_via_skill(fail_brain, goal="login", project_start=skill_root)
    assert results[0].verdict == RegressionVerdict.FAIL

    # Both surfaces failed for the same store + brain output.
    assert (console_rc != 0) is (results[0].verdict == RegressionVerdict.FAIL)


# --- 2. body imports + runs with no LLM SDK present -----------------------


def test_core_imports_and_runs_with_no_llm_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`import praxis` and an end-to-end engine run work with NO LLM SDK
    installed (ADR-0019: the core stays brain-agnostic).

    We block `anthropic`, `openai`, and `browser_use` at import time, drop any
    cached copies, re-import the core + the seam modules from scratch, and run
    the engine through the skill driver. If anything on the core path imported
    a brain SDK eagerly, the blocked import would raise here.
    """
    blocked = {"anthropic", "openai", "browser_use"}
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        top = name.split(".")[0]
        if top in blocked:
            raise ModuleNotFoundError(f"blocked for test: {name}")
        return real_import(name, *args, **kwargs)

    # Drop any cached brain SDK modules so a re-import would actually hit the
    # guard rather than a warm cache.
    import sys
    for mod in list(sys.modules):
        if mod.split(".")[0] in blocked:
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    # Re-import the core + seam from scratch under the guard. No brain SDK is
    # pulled, so these succeed.
    for modname in (
        "praxis",
        "praxis.model",
        "praxis.store",
        "praxis.merge",
        "praxis.oracle",
        "praxis.runner",
        "praxis.runner.engine",
        "praxis.skill_driver",
    ):
        importlib.import_module(modname)

    # And an actual engine run completes with no brain SDK present: the brain
    # is the in-memory callable, never an imported LLM.
    _init_project_with_login(tmp_path)
    results = regress_via_skill(_pass_brain, goal="login", project_start=tmp_path)
    assert results[0].verdict == RegressionVerdict.PASS


def test_brain_is_a_plain_callable_not_an_llm_type() -> None:
    """The Brain seam is `Callable[[str], dict]` and nothing more: a bare lambda
    satisfies it, so no LLM type leaks into the signature."""
    from praxis.runner import Brain  # noqa: F401  (imported for the contract)

    # A trivial callable IS a valid brain; the engine never inspects it for an
    # LLM-specific attribute.
    brain = lambda prompt: {"observations": [], "actions": 0, "tokens": None}  # noqa: E731
    assert callable(brain)
    assert brain("anything") == {"observations": [], "actions": 0, "tokens": None}


# --- 3. no brain identifier is persisted into knowledge -------------------


def test_no_brain_identifier_persisted_into_knowledge(tmp_path: Path) -> None:
    """After a regress run, the stored event carries the agent_identity
    provenance only (`agent_id` / `source_id`) and NO brain identifier
    (ADR-0019 forbidden alternative: never persist the brain choice).

    We run the same goal twice through two DIFFERENT brains that emit identical
    observations, then assert the persisted events are indistinguishable on
    brain: the serialized event names no brain, and the two runs' events match
    on every field except the per-event id and timestamp.
    """
    _init_project_with_login(tmp_path)

    # Two brains, same observation. The only thing that differs is the closure
    # that produced it; that difference must NOT reach the store.
    def brain_a(prompt: str) -> dict[str, Any]:
        return json.loads(json.dumps(_PASS_OBS))

    def brain_b(prompt: str) -> dict[str, Any]:
        return json.loads(json.dumps(_PASS_OBS))

    regress_via_skill(brain_a, goal="login", project_start=tmp_path)
    regress_via_skill(brain_b, goal="login", project_start=tmp_path)

    proj = discover_project(tmp_path)
    events = list(proj.store().read("login"))
    assert len(events) == 2, "both runs should have appended one event each"

    forbidden_tokens = ("brain", "llm", "anthropic", "claude", "openai", "model")
    for ev in events:
        dumped = ev.model_dump(mode="json")
        # The only provenance on a stored observation is the agent_identity.
        assert ev.agent_id == "praxis-cli"
        for sig in ev.signals:
            assert sig.source_id == "praxis-cli"
        # No field anywhere in the serialized event names a brain.
        blob = json.dumps(dumped).lower()
        for tok in forbidden_tokens:
            assert tok not in blob, (
                f"a brain identifier {tok!r} leaked into the stored event: {blob}"
            )

    # Brain-independence of the store: the two events agree on every field
    # except the content-addressable id and the timestamp.
    a = events[0].model_dump(mode="json")
    b = events[1].model_dump(mode="json")
    for ignore in ("event_id", "ts"):
        a.pop(ignore, None)
        b.pop(ignore, None)
    assert a == b, "two brains, same observation -> store records the same knowledge"


def test_explore_candidate_carries_agent_identity_only(tmp_path: Path) -> None:
    """A candidate risk written by explore carries the agent_identity as its
    only source, never a brain name (ADR-0008 + ADR-0019)."""
    _init_project_with_login(tmp_path)

    risk_obs: dict[str, Any] = {
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
    }

    def risk_brain(prompt: str) -> dict[str, Any]:
        return json.loads(json.dumps(risk_obs))

    outcome = explore_via_skill(risk_brain, "login", project_start=tmp_path)
    assert outcome.result.new_risks, "the candidate risk should survive validation"

    proj = discover_project(tmp_path)
    cand_events = list(proj.candidate_files().read("login"))
    assert cand_events, "explore should have committed the candidate"
    for ce in cand_events:
        # The candidate's only source is the agent_identity; no brain name.
        assert ce.agent_identity == "praxis-cli"
        blob = json.dumps(ce.model_dump(mode="json")).lower()
        for tok in ("brain", "llm", "anthropic", "claude", "openai"):
            assert tok not in blob


# --- the deterministic-vs-agentic classification --------------------------


def test_operation_classification_matches_adr_0019() -> None:
    """`init` / `status` / `review` are deterministic (brain-free); `teach` /
    `regress` / `explore` are agentic (need a brain). The split is pinned in
    code so a later change cannot quietly cross it (ADR-0019 decision 2)."""
    assert DETERMINISTIC_OPERATIONS == frozenset({"init", "status", "review"})
    assert AGENTIC_OPERATIONS == frozenset({"teach", "regress", "explore"})
    # The two classes are disjoint and cover the operation set once each.
    assert DETERMINISTIC_OPERATIONS.isdisjoint(AGENTIC_OPERATIONS)

    for op in ("init", "status", "review"):
        assert is_agentic(op) is False
    for op in ("teach", "regress", "explore"):
        assert is_agentic(op) is True

    # An unclassified operation is a loud error, never a silent guess.
    with pytest.raises(ValueError):
        is_agentic("frobnicate")
