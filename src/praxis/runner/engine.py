"""The brain seam and the dual-surface execution engine (ADR-0019).

ADR-0019 fixes two things this module makes concrete in code:

1. **The body is brain-agnostic; the brain is pluggable.** The "brain" is the
   reasoning LLM that decides what to click, when it is stuck, and whether the
   happy path was observed. It is NOT compiled into the core and NOT recorded
   in knowledge. The seam through which a brain plugs in is the executor
   already used by `RegressionRunner` / `ExplorationRunner`: a single callable
   that takes the rendered, steps-free prompt and returns the agent's observed
   JSON. This module names that seam `Brain` so the contract is explicit. The
   core imports and tests with NO LLM SDK present, exactly as ADR-0003 kept it
   testable with no browser; the LLM lives behind an optional extra (`live`)
   and a Claude Code skill surface, never here.

2. **The operations split into a deterministic class and an agentic class.**
   `init` / `status` / `review` read and report; they need no brain. `regress`
   / `explore` reason against a live app; they take a brain via the seam. The
   two frozensets below pin that classification so a later change cannot
   quietly hand a brain to a deterministic op or drop the brain from an agentic
   one.

The same engine functions (`regress_engine`, `explore_engine`) are called from
BOTH surfaces ADR-0019 decision 4 names:

- the **console CLI** (`praxis regress`, `praxis explore`): the brain is the
  paste/file executor or, in CI, the API-key agent (`live` extra);
- a **direct-call skill driver** (`/praxis:regress`, `/praxis:explore`): the
  brain is the local Claude Code session.

Same body, same store reads and writes, same verdict. Only the brain that
drives the seam differs. Choosing a brain never changes what is stored: the
brain choice is at most execution provenance (`source_id = agent_identity`,
ADR-0009 / ADR-0014), never a field the projection or the oracle gate reads,
and this module never writes a brain identifier into knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..adapters.spi import KnowledgeAdapter
from .exploration import ExplorationResult, ExplorationRunner
from .regression import RegressionRunner, RegressionVerdict, RunResult

# --- the brain seam -------------------------------------------------------

# A Brain is the single pluggable seam between the brain-agnostic body and
# whichever LLM drives an agentic operation. It receives the rendered,
# steps-free prompt and returns the agent's observed JSON (the same dict shape
# the runners already parse). This is a plain callable on purpose: no LLM type
# leaks into the core, so `import praxis` and the body tests work with no LLM
# SDK installed. The console paste/file executors, the CI API-key agent, and a
# Claude Code skill driver all satisfy this one type.
Brain = Callable[[str], dict[str, Any]]

# ADR-0019 decision 2: the operations partition on whether they reason.
# Pinned here so surface selection (decisions 4 and 5) cannot drift: a
# deterministic op must never be handed a brain, and an agentic op must always
# get one through the seam.
DETERMINISTIC_OPERATIONS: frozenset[str] = frozenset({"init", "status", "review"})
AGENTIC_OPERATIONS: frozenset[str] = frozenset({"teach", "regress", "explore"})


def is_agentic(operation: str) -> bool:
    """True if `operation` reasons against a live app and so needs a brain.

    Raises ValueError on an unknown operation rather than guessing: an op that
    is in neither class is a classification gap ADR-0019 decision 2 forbids.
    """
    if operation in AGENTIC_OPERATIONS:
        return True
    if operation in DETERMINISTIC_OPERATIONS:
        return False
    raise ValueError(
        f"unknown operation {operation!r}: classify it as deterministic or "
        f"agentic (ADR-0019 decision 2) before routing it"
    )


# --- the dual-surface engine ----------------------------------------------


def regress_engine(
    adapter: KnowledgeAdapter,
    brain: Brain,
    goals: list[str],
    *,
    agent_id: str = "praxis-regress",
    observed_app_version: str | None = None,
    budget_tokens: int | None = None,
    budget_actions: int | None = None,
    stop_on_fail: bool = False,
) -> list[RunResult]:
    """Run R-mode across `goals` driven by `brain`, surface-independent.

    This is the single engine the console `praxis regress` and a direct-call
    skill driver both call. Given the same adapter (same store) and the same
    brain output, it returns the same verdicts regardless of which surface
    invoked it. The verdict is computed deterministically from the brain's
    observations by `verdict_from_observations`; the brain only supplies what
    it observed, never the verdict, and never a brain identifier in knowledge.
    """
    runner = RegressionRunner(
        adapter, agent_id=agent_id, observed_app_version=observed_app_version,
    )
    return runner.run_all(
        goals, brain,
        budget_tokens=budget_tokens,
        budget_actions=budget_actions,
        stop_on_fail=stop_on_fail,
    )


def regress_failed(results: list[RunResult]) -> bool:
    """True if any goal regressed. The CLI exit code and a skill triage both
    use this so the red/green decision is computed in one place across the two
    surfaces (ADR-0019 decision 4)."""
    return any(r.verdict == RegressionVerdict.FAIL for r in results)


@dataclass
class ExploreOutcome:
    """An E-mode run plus the candidate events the engine mirrored to the
    committed tree, so both surfaces report the same committed count.

    `committed_paths` is empty when no committed-candidate sink was provided
    (the seam stays optional; a bare brain run still works)."""

    result: ExplorationResult
    committed_paths: list[Any] = field(default_factory=list)


# A committed-candidate sink mirrors this run's newly written candidate events
# from the per-machine log into the shared `.praxis/candidates/<goal>/` tree
# (ADR-0021 decision 4). The CLI supplies one; a test or a bare skill run may
# omit it. Keeping it a plain callable keeps the engine free of any concrete
# store type, so the seam stays as small as the runner's.
CommittedSink = Callable[[str], list[Any]]


def explore_engine(
    adapter: KnowledgeAdapter,
    brain: Brain,
    goal: str,
    *,
    agent_id: str = "praxis-explore",
    observed_app_version: str | None = None,
    happy_path_urls: list[str] | None = None,
    budget_tokens: int | None = None,
    budget_actions: int | None = None,
    committed_sink: CommittedSink | None = None,
) -> ExploreOutcome:
    """Run E-mode for one goal driven by `brain`, surface-independent.

    Same engine for the console `praxis explore` and a direct-call skill
    driver. The runner forces `source_id = agent_identity` on emitted
    candidates (ADR-0008), so N same-brain runs count as ONE source; the brain
    choice never becomes a stored field. `committed_sink`, when provided,
    mirrors this run's new candidate events into the committed tree and is run
    after the brain returns, so both surfaces produce the same committed count.
    """
    runner = ExplorationRunner(
        adapter, agent_id=agent_id, observed_app_version=observed_app_version,
    )
    result = runner.run_one(
        goal, brain,
        happy_path_urls=happy_path_urls,
        budget_tokens=budget_tokens,
        budget_actions=budget_actions,
    )
    committed: list[Any] = []
    if committed_sink is not None:
        committed = committed_sink(goal)
    return ExploreOutcome(result=result, committed_paths=committed)
