"""R-mode (regression) runner.

Contract:
  inputs  - believed success_signals + failure_signals for one or more goals.
  outputs - per goal: pass / fail / uncertain verdict + observations + a run
            record. The runner does NOT drive a browser; it renders prompts
            and reads the store. The executor (subscription Claude Code path
            or API-key path) feeds observations into the store via
            `adapter.write_observations(...)`; the runner then computes the
            verdict deterministically from those observations.

The verdict logic is deliberately small (ADR-0009 sec 3 + AGENTS.md
non-negotiable 5): a failure signal observed = fail; all success signals
observed = pass; otherwise = uncertain. We do NOT call the oracle inside the
runner: the oracle gates BELIEF (which signals become trusted across runs),
not the per-run verdict. Mixing them silently is the wrong-oracle vector
docs/06 warns about.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable

from ..adapters.spi import KnowledgeAdapter
from ..model import KnowledgeFile, Signal, Status
from ..store import ObservedSignal
from .prompts import render_regression_prompt


class RegressionVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


@dataclass
class RunResult:
    """The per-goal outcome of an R-mode run."""

    goal_id: str
    verdict: RegressionVerdict
    actions: int
    tokens: int | None
    wall_seconds: float
    observed_signals: list[ObservedSignal] = field(default_factory=list)
    matched_success: list[str] = field(default_factory=list)
    matched_failure: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# An executor receives the rendered prompt and returns what the agent observed.
# Tests pass a fake executor; the LOCAL_RUN protocol wires a real one through
# Claude Code + Playwright MCP; an API-key path can wire an LLM-driven loop.
ExecutorResult = dict
Executor = Callable[[str], ExecutorResult]


# Floor for word-overlap between a paraphrased agent observation and a
# SEED signal value (success_signals / failure_signals from the
# knowledge file). Phase-1 product surface: this decides whether
# `praxis regress` counts a real-world observation as matching the
# documented oracle. Tuned so "sign-out becomes available" matches
# "a sign-out action becomes available" (most content words in common).
#
# DELIBERATELY DIFFERENT from `experiments.regression_recall.metrics.
# PARAPHRASE_FLOOR` (0.6), which adjudicates observations against a
# pre-registered manifest in the experiment harness. The runner is
# lenient (real agent paraphrase varies a lot); the experiment matcher
# is strict (the manifest pins canonical phrasing to keep the falsifier
# rigorous). Both fall back to the LLM-judge for ambiguous cases.
_PARAPHRASE_THRESHOLD = 0.5

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "and", "or", "of", "to", "in", "on",
    "with", "for", "by", "at", "as", "be", "this", "that",
})


def _tokens(s: str) -> set[str]:
    out: set[str] = set()
    cur: list[str] = []
    for ch in s.lower():
        if ch.isalnum() or ch == "/":
            cur.append(ch)
        else:
            if cur:
                out.add("".join(cur))
                cur.clear()
    if cur:
        out.add("".join(cur))
    return {t for t in out if t and t not in _STOPWORDS}


def _value_matches(observed: ObservedSignal, target: Signal) -> bool:
    """A coarse match: same `type` and Jaccard word-overlap above the floor.

    Phase-1 keeps this simple - value strings are short semantic phrases
    ("a sign-out action becomes available"). Exact equality is too strict
    (agents paraphrase); substring containment misses common cases ("sign-out
    becomes available" is not a substring of "a sign-out action becomes
    available"). Jaccard on content tokens captures both.
    """
    if observed.type != target.type:
        return False
    a = _tokens(observed.value)
    b = _tokens(target.value)
    if not a or not b:
        return False
    inter = len(a & b)
    union = len(a | b)
    return (inter / union) >= _PARAPHRASE_THRESHOLD


def verdict_from_observations(
    kf: KnowledgeFile,
    observations: Iterable[ObservedSignal],
) -> tuple[RegressionVerdict, list[str], list[str]]:
    """Compute the verdict for one goal from its observations.

    Returns (verdict, matched_success_values, matched_failure_values).

    Rule (ADR-0009):
      - any failure signal observed as `present=True` -> FAIL
      - all believed success signals observed as `present=True` -> PASS
      - otherwise -> UNCERTAIN (oracle could not be exercised; not a regression
        but also not a clean pass)
    """
    obs = [o for o in observations if o.present]
    matched_success: list[str] = []
    matched_failure: list[str] = []

    failure_targets = [s for s in (kf.failure_signals or [])
                       if s.status in (Status.BELIEVED, Status.CONTESTED)]
    for ft in failure_targets:
        if any(_value_matches(o, ft) and o.kind == "failure" for o in obs):
            matched_failure.append(ft.value)
    if matched_failure:
        return RegressionVerdict.FAIL, matched_success, matched_failure

    success_targets = [s for s in kf.success_signals if s.status == Status.BELIEVED]
    for st in success_targets:
        if any(_value_matches(o, st) and o.kind == "success" for o in obs):
            matched_success.append(st.value)

    if success_targets and len(matched_success) == len(success_targets):
        return RegressionVerdict.PASS, matched_success, matched_failure
    return RegressionVerdict.UNCERTAIN, matched_success, matched_failure


@dataclass
class _RunContext:
    """What the executor returned, as a typed wrapper around the dict."""

    observations: list[ObservedSignal]
    actions: int
    tokens: int | None
    notes: list[str]


def _parse_executor_result(raw: ExecutorResult) -> _RunContext:
    obs_raw = raw.get("observations", [])
    obs: list[ObservedSignal] = []
    for o in obs_raw:
        if isinstance(o, ObservedSignal):
            obs.append(o)
        else:
            obs.append(ObservedSignal.model_validate(o))
    return _RunContext(
        observations=obs,
        actions=int(raw.get("actions", 0)),
        tokens=raw.get("tokens"),
        notes=list(raw.get("notes", [])),
    )


class RegressionRunner:
    """Runs R-mode across one or more goals using an adapter + executor.

    The runner is a coordinator: it asks the adapter for believed knowledge,
    renders the prompt, calls the executor (which is where the agent actually
    runs), persists observations, computes the verdict, and emits a RunResult.

    The executor protocol is deliberately small (one function, one dict in,
    one dict out) so the LOCAL_RUN.md subscription path and an API-key path
    can satisfy it without growing the runner.
    """

    def __init__(self, adapter: KnowledgeAdapter, *, agent_id: str = "praxis-regress",
                 observed_app_version: str | None = None) -> None:
        self.adapter = adapter
        self.agent_id = agent_id
        self.observed_app_version = observed_app_version

    def run_one(self, goal_id: str, executor: Executor, *,
                budget_actions: int | None = None,
                budget_tokens: int | None = None,
                persist_observations: bool = True) -> RunResult:
        kf = self.adapter.read_knowledge(goal_id)
        if kf is None:
            raise ValueError(
                f"no believed knowledge for goal {goal_id!r}; seed it with `praxis learn`"
            )

        prompt = render_regression_prompt(
            kf, budget_actions=budget_actions, budget_tokens=budget_tokens,
        )

        t0 = time.monotonic()
        started_at = datetime.now(timezone.utc)
        raw = executor(prompt)
        wall = time.monotonic() - t0
        ended_at = datetime.now(timezone.utc)

        ctx = _parse_executor_result(raw)

        if persist_observations and ctx.observations:
            self.adapter.write_observations(
                goal_id=goal_id,
                agent_id=self.agent_id,
                observations=ctx.observations,
                observed_app_version=self.observed_app_version,
            )

        verdict, matched_success, matched_failure = verdict_from_observations(
            kf, ctx.observations,
        )

        return RunResult(
            goal_id=goal_id,
            verdict=verdict,
            actions=ctx.actions,
            tokens=ctx.tokens,
            wall_seconds=wall,
            observed_signals=ctx.observations,
            matched_success=matched_success,
            matched_failure=matched_failure,
            notes=ctx.notes,
            started_at=started_at,
            ended_at=ended_at,
        )

    def run_all(self, goal_ids: list[str], executor: Executor, *,
                 budget_actions: int | None = None,
                 budget_tokens: int | None = None,
                 stop_on_fail: bool = False) -> list[RunResult]:
        results: list[RunResult] = []
        for gid in goal_ids:
            r = self.run_one(
                gid, executor,
                budget_actions=budget_actions, budget_tokens=budget_tokens,
            )
            results.append(r)
            if stop_on_fail and r.verdict == RegressionVerdict.FAIL:
                break
        return results
