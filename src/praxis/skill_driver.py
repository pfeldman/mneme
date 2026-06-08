"""Direct-call entry points for the local-brain skill surface (ADR-0019).

ADR-0019 decision 4 puts the agentic operations (`regress`, `explore`) on TWO
surfaces over the SAME engine: the console CLI and a Claude Code skill. The
console surface is `praxis.cli.main` (argparse, a process exit code). This
module is the OTHER surface: a thin, importable driver a skill calls
directly, handing in the local Claude session as the brain.

There is no LLM here. The brain is a parameter, the same `Brain` seam the
engine names: a callable that takes the rendered, steps-free prompt and
returns the agent's observed JSON. The skill (the Claude Code session) is the
caller that supplies that callable; this module never imports an LLM SDK, so
`import praxis.skill_driver` works with no brain installed.

Both surfaces reuse `praxis.cli.main.discover_project` to resolve the project
and `praxis.cli.main.ProjectContext` to build the adapter and the committed
candidate sink, so the console run and the skill run read and write the SAME
store and produce the SAME verdict for the same goal + brain output.
"""
from __future__ import annotations

from pathlib import Path

from .runner import (
    Brain,
    ExploreOutcome,
    RunResult,
    explore_engine,
    regress_engine,
)

__all__ = [
    "regress_via_skill",
    "explore_via_skill",
]


def regress_via_skill(
    brain: Brain,
    *,
    goal: str | None = None,
    project_start: Path | None = None,
    budget_tokens: int | None = None,
    budget_actions: int | None = None,
    stop_on_fail: bool = False,
) -> list[RunResult]:
    """Run R-mode from the skill surface, driven by `brain`.

    Discovers the `.praxis/` project (upward from `project_start` or cwd),
    builds the same adapter the console uses, and calls the same engine. With
    `goal=None` it regresses every seeded goal, matching the console default.
    Returns the per-goal results; the skill triages the non-OK ones (ADR-0023).
    The brain choice is never written into knowledge.
    """
    # Imported here, not at module top, so this driver carries no console
    # dependency at import time and stays a leaf the skill can load cheaply.
    from .cli.main import discover_project

    proj = discover_project(project_start)
    adapter = proj.adapter()
    goals = [goal] if goal else sorted(proj.seeds().keys())
    if not goals:
        raise ValueError(
            "no goals to regress (no seeds in .praxis/knowledge/); "
            "seed one with `praxis learn` first"
        )
    return regress_engine(
        adapter, brain, goals,
        agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
        budget_tokens=budget_tokens,
        budget_actions=budget_actions,
        stop_on_fail=stop_on_fail,
    )


def explore_via_skill(
    brain: Brain,
    goal: str,
    *,
    project_start: Path | None = None,
    happy_path_urls: list[str] | None = None,
    budget_tokens: int | None = None,
    budget_actions: int | None = None,
) -> ExploreOutcome:
    """Run E-mode for one goal from the skill surface, driven by `brain`.

    Same engine and same committed-candidate mirror as the console
    `praxis explore`, so a skill run writes one file per observation into the
    committed tree exactly as the console does (ADR-0021 decision 4). The
    runner forces `source_id = agent_identity`, so N same-brain runs stay ONE
    source (ADR-0008) and the brain choice never becomes a stored field.
    """
    from .cli.main import discover_project

    proj = discover_project(project_start)
    adapter = proj.adapter()
    store = proj.store()
    before_ids = {ev.event_id for ev in store.read_candidates(goal)}

    def _commit_new_candidates(g: str) -> list[Path]:
        new_events = [
            ev for ev in store.read_candidates(g)
            if ev.event_id not in before_ids
        ]
        return proj.candidate_files().write_all(new_events)

    return explore_engine(
        adapter, brain, goal,
        agent_id=proj.agent_id,
        observed_app_version=proj.observed_app_version,
        happy_path_urls=happy_path_urls,
        budget_tokens=budget_tokens,
        budget_actions=budget_actions,
        committed_sink=_commit_new_candidates,
    )
