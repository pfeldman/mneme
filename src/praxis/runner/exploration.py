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

        # ADR-0014: agent-proposed risks and uncertainties become durable
        # CandidateEvents (sibling to ObservationEvent). The runner forces
        # `agent_identity = self.agent_id` so the source-independence rule
        # (ADR-0008) binds here too: N same-model E-mode runs count as ONE
        # source under `independent_diverse(...)`, never self-promote.
        if persist_observations and (new_risks or new_uncertainties):
            write = getattr(self.adapter, "write_candidates", None)
            if write is not None:
                write(
                    goal_id=goal_id,
                    agent_identity=self.agent_id,
                    new_risks=new_risks,
                    new_uncertainties=new_uncertainties,
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

    def run_all(self, goal_ids: list[str], executor: Executor, *,
                happy_path_urls: list[str] | None = None,
                budget_tokens: int | None = None,
                budget_actions: int | None = None,
                persist_observations: bool = True) -> list[ExplorationResult]:
        """Run E-mode across `goal_ids` in order, one ExplorationResult each.

        The simple sequential counterpart to `RegressionRunner.run_all`. The
        aggregate default-all path (`run_explore_aggregate`) wraps this with
        per-goal error isolation so one goal that throws does not kill the run.
        """
        results: list[ExplorationResult] = []
        for gid in goal_ids:
            results.append(self.run_one(
                gid, executor,
                happy_path_urls=happy_path_urls,
                budget_tokens=budget_tokens,
                budget_actions=budget_actions,
                persist_observations=persist_observations,
            ))
        return results


# --- the aggregate (default-all) explore run (ADR-0023 decision 2, 8) -------


@dataclass
class ExploreGoalOutcome:
    """The per-goal outcome of a default-all explore run.

    Carries EITHER a completed `ExplorationResult` (the goal ran within its
    budget slice) OR an `error` string (the goal could not run: no believed
    knowledge, the brain threw, or the goal exhausted its per-goal token / wall
    slice per ADR-0023 decision 7). A failed goal is surfaced, never silently
    dropped, so the aggregate report names every goal it attempted (the same
    loud-over-silent posture R-mode's `run_aggregate` takes for ERROR goals).
    """

    goal_id: str
    result: ExplorationResult | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.error is None


def run_explore_aggregate(
    runner: "ExplorationRunner",
    goal_ids: list[str],
    executor: Executor,
    *,
    happy_path_urls_for: Callable[[str], list[str]] | None = None,
    budget_tokens_per_goal: int | None = None,
    budget_actions_per_goal: int | None = None,
    budget_wall_seconds_per_goal: float | None = None,
) -> list[ExploreGoalOutcome]:
    """Hunt off-happy-path across EVERY believed goal (ADR-0023 decision 2).

    With no `--goal`, explore runs every goal under `.praxis/knowledge/` and
    returns one outcome per goal. Each goal runs in its own try block so a goal
    whose brain throws becomes a surfaced error for that goal, not a crash that
    drops the rest of the run (ADR-0023 decision 4's loud-over-silent posture,
    applied to E-mode: a goal that cannot run is named, never silently skipped).
    The `off_path_fraction` floor (ADR-0009) is preserved per goal inside each
    `ExplorationResult`.

    `happy_path_urls_for` lets the caller supply the believed happy-path URLs
    for each goal (used by `compute_off_path_fraction`); when None, every goal
    runs with an empty happy path. Order of `goal_ids` is preserved so the
    report is stable across runs.

    Per-goal budget slice (ADR-0023 decision 7), mirroring the regress
    aggregate (`regression.run_aggregate`): each goal gets its OWN token and
    wall-time slice, not a shared pool the goals race for, so one expensive or
    pathological goal cannot starve the rest. The slice is enforced as a
    post-hoc cap because the executor is a single opaque call the runner cannot
    interrupt mid-flight: a goal whose run exceeds its token or wall slice is
    surfaced as a loud budget-exhaustion ERROR for that goal (`ok=False`),
    never returned as a clean success and never silently counted as explored.
    `ExplorationResult` already carries `.tokens` and `.wall_seconds`, so the
    cap reads the same dimensions the regress aggregate does. Because the
    committed-candidate mirror downstream runs only for `ok` outcomes, an
    exhausted goal's candidate files are not mirrored to the shared tree as a
    clean success; the budget verdict is loud and traceable, not convenient.
    """
    outcomes: list[ExploreGoalOutcome] = []
    for gid in goal_ids:
        happy = happy_path_urls_for(gid) if happy_path_urls_for else None
        try:
            result = runner.run_one(
                gid, executor,
                happy_path_urls=happy,
                budget_tokens=budget_tokens_per_goal,
                budget_actions=budget_actions_per_goal,
            )
        except Exception as exc:  # noqa: BLE001 - a thrown goal is surfaced, not dropped
            outcomes.append(ExploreGoalOutcome(
                goal_id=gid,
                error=f"could not explore: {type(exc).__name__}: {exc}",
            ))
            continue

        # Per-goal budget enforcement (ADR-0023 decision 7): a goal that
        # exhausted its token or wall slice is a loud ERROR, not a trusted
        # success. Same post-hoc cap and same two dimensions the regress
        # aggregate applies (regression.run_aggregate ~lines 588-612).
        exhausted: list[str] = []
        if (budget_tokens_per_goal is not None and result.tokens is not None
                and result.tokens > budget_tokens_per_goal):
            exhausted.append(
                f"tokens {result.tokens} > slice {budget_tokens_per_goal}"
            )
        if (budget_wall_seconds_per_goal is not None
                and result.wall_seconds > budget_wall_seconds_per_goal):
            exhausted.append(
                f"wall {result.wall_seconds:.2f}s > slice "
                f"{budget_wall_seconds_per_goal:.2f}s"
            )
        if exhausted:
            outcomes.append(ExploreGoalOutcome(
                goal_id=gid,
                error="per-goal budget exhausted: " + "; ".join(exhausted),
            ))
            continue

        outcomes.append(ExploreGoalOutcome(goal_id=gid, result=result))
    return outcomes
