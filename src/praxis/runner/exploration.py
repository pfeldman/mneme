"""E-mode (exploration) runner.

Contract:
  inputs  - believed + contested risks (with structured triggers), open
            uncertainties, the failure-signal watch-list, a budget.
  outputs - candidate signal observations (kind=failure) when a risk's
            `expect` predicate fires; new candidate risks the agent discovered
            (status=contested, structured trigger required); new
            uncertainties the agent could not resolve; an
            `off_path_fraction` observability metric.

ADR-0009 sec 3: same diversity-or-seed gate for promotion (ADR-0005, 0008).
The runner sets `source_id = agent_identity` on emitted observations, NOT
`run_uuid`, so multi-run same-source repeats do NOT self-promote (this is
the source-independence invariant that ADR-0008 hardened).

The `off_path_fraction` metric is the floor that catches E-mode collapsing
into R-mode (a pre-registered kill criterion of the regression-recall
experiment, docs/phase-1-experiment.md). It does not gate anything in the
runner itself - the harness reads it post-run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..adapters.spi import KnowledgeAdapter
from ..model import Risk, Status, Uncertainty
from ..model.trigger_validator import validate_risk
from ..store import ObservedSignal
from .prompts import render_exploration_prompt


@dataclass
class ExplorationResult:
    """The per-goal outcome of an E-mode run."""

    goal_id: str
    actions: int
    tokens: int | None
    wall_seconds: float
    off_path_fraction: float
    candidate_observations: list[ObservedSignal] = field(default_factory=list)
    new_risks: list[Risk] = field(default_factory=list)
    new_uncertainties: list[Uncertainty] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    visited_urls: list[str] = field(default_factory=list)
    happy_path_urls: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Executor = Callable[[str], dict[str, Any]]


def compute_off_path_fraction(visited: list[str], happy_path: list[str]) -> float:
    """Fraction of visited URLs that are NOT on the believed happy path.

    Returns 1.0 if happy_path is empty (no known happy path -> everything is
    off-path by definition; that is the cold-discovery case, not the
    degenerate "E-mode is R-mode" case). Returns 0.0 if no URLs were
    visited at all.
    """
    if not visited:
        return 0.0
    if not happy_path:
        return 1.0
    happy = {u.rstrip("/") for u in happy_path}
    off = sum(1 for u in visited if u.rstrip("/") not in happy)
    return off / len(visited)


class ExplorationRunner:
    """Runs E-mode against believed knowledge using an adapter + executor.

    Same coordinator shape as RegressionRunner: read knowledge, render prompt,
    delegate execution, ingest candidates. The asymmetry is the output:
    R-mode produces a verdict; E-mode produces candidate knowledge that
    enters the store as contested and is promoted only by independent
    corroboration (the runner does NOT promote here, the projection does).
    """

    def __init__(self, adapter: KnowledgeAdapter, *, agent_id: str = "praxis-explore",
                 observed_app_version: str | None = None) -> None:
        self.adapter = adapter
        self.agent_id = agent_id
        self.observed_app_version = observed_app_version

    def run_one(self, goal_id: str, executor: Executor, *,
                happy_path_urls: list[str] | None = None,
                budget_actions: int | None = None,
                budget_tokens: int | None = None,
                persist_observations: bool = True) -> ExplorationResult:
        kf = self.adapter.read_knowledge(goal_id)
        if kf is None:
            raise ValueError(
                f"no believed knowledge for goal {goal_id!r}; seed it with `praxis learn`"
            )

        prompt = render_exploration_prompt(
            kf, budget_actions=budget_actions, budget_tokens=budget_tokens,
        )

        t0 = time.monotonic()
        started_at = datetime.now(timezone.utc)
        raw = executor(prompt)
        wall = time.monotonic() - t0
        ended_at = datetime.now(timezone.utc)

        # Parse executor outputs. The executor protocol mirrors the R-mode one
        # plus exploration-specific fields.
        candidates_raw = raw.get("candidate_observations", [])
        candidates: list[ObservedSignal] = [
            o if isinstance(o, ObservedSignal) else ObservedSignal.model_validate(o)
            for o in candidates_raw
        ]
        new_risks_raw = raw.get("new_risks", [])
        new_risks_unvalidated: list[Risk] = [
            r if isinstance(r, Risk) else Risk.model_validate(r)
            for r in new_risks_raw
        ]
        # Drop new risks whose trigger fails the banned-phrase validator
        # (ADR-0009 sec 4). A risk like `expect: "under high load"` slides
        # past the discriminated-union shape check but is exactly the
        # schema-rot vector the validator exists to refuse. Rejected risks
        # surface in `notes` so the operator can see why and rephrase.
        new_risks: list[Risk] = []
        rejected_notes: list[str] = list(raw.get("notes", []))
        for r in new_risks_unvalidated:
            outcome = validate_risk(r)
            if outcome.outcome == "rejected":
                rejected_notes.append(
                    f"REJECTED new risk {r.id!r}: {outcome.reason}"
                )
                continue
            # New risks emitted by an agent must enter as `contested`, never
            # `believed`, regardless of what the executor said. This enforces
            # ADR-0008: a single source cannot self-corroborate.
            if r.status == Status.BELIEVED:
                r.status = Status.CONTESTED
            new_risks.append(r)

        new_uncertainties_raw = raw.get("new_uncertainties", [])
        new_uncertainties: list[Uncertainty] = [
            u if isinstance(u, Uncertainty) else Uncertainty.model_validate(u)
            for u in new_uncertainties_raw
        ]
        visited = list(raw.get("visited_urls", []))
        actions = int(raw.get("actions", 0))
        tokens = raw.get("tokens")
        notes = rejected_notes

        if persist_observations and candidates:
            self.adapter.write_observations(
                goal_id=goal_id,
                agent_id=self.agent_id,
                observations=candidates,
                observed_app_version=self.observed_app_version,
            )

        off_path = compute_off_path_fraction(visited, happy_path_urls or [])

        return ExplorationResult(
            goal_id=goal_id,
            actions=actions,
            tokens=tokens,
            wall_seconds=wall,
            off_path_fraction=off_path,
            candidate_observations=candidates,
            new_risks=new_risks,
            new_uncertainties=new_uncertainties,
            notes=notes,
            visited_urls=visited,
            happy_path_urls=list(happy_path_urls or []),
            started_at=started_at,
            ended_at=ended_at,
        )
