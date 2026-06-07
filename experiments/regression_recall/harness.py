"""Regression-recall experiment harness.

What this module does:
  - Defines an Executor protocol that each arm satisfies (cold,
    cold_readme, memory). The harness does not drive the browser; it
    composes the executor's outputs into RunSummary records and feeds
    them to `metrics.evaluate`.
  - Iterates over (release, arm, seed, goal), calls the executor with
    the appropriate prompt, persists the result.
  - Computes aggregates + verdict at the end and writes results.md +
    results.json.

The Executor abstraction is the seam between the harness (deterministic,
auditable plumbing) and the agent (Claude Code subscription path via
Playwright MCP, or an API-key LLM loop). Both paths satisfy the same
contract; both are testable offline with fake executors.

What this module deliberately does NOT do:
  - Run actual LLM calls. The subscription path puts the human in the
    loop (`PHASE_1_LOCAL_RUN.md` is the protocol); the API path is a
    separate executor implementation that this module accepts.
  - Toggle planted regressions or call the testapp directly. The
    harness OPERATOR is responsible for /_plant before each arm run and
    /_unplant after. The harness asks the executor to record what it
    observed; the operator's responsibility is to set up the world.

Reading order: design rationale in docs/phase-1-experiment.md;
pre-registration in pre_registration.md.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .manifest import Manifest, default_manifest
from .metrics import (
    Arm,
    ArmAggregate,
    Detection,
    RunSummary,
    aggregate,
    evaluate,
    match_against_manifest,
    write_results_markdown,
)

# An Executor receives (arm, goal_id, prompt) and returns the raw outputs the
# agent emitted on that goal: observations, visited urls, actions, tokens.
# The harness adapts those into a RunSummary.
ExecutorInput = dict[str, Any]
ExecutorOutput = dict[str, Any]
Executor = Callable[[Arm, str, str, ExecutorInput], ExecutorOutput]


@dataclass
class GoalSpec:
    """One goal under test: id + an optional happy-path URL list for the
    off_path_fraction floor on the memory arm."""

    goal_id: str
    happy_path_urls: list[str] = field(default_factory=list)


@dataclass
class RunPlan:
    """A pre-registered plan: which arms, seeds, goals, budget."""

    release: str
    arms: tuple[Arm, ...]
    seeds: tuple[int, ...]
    goals: tuple[GoalSpec, ...]
    budget_tokens_per_goal: int
    is_control: bool = False  # true for the unmutated-control release


def build_default_plan(*, release: str = "phase-1-r1",
                       budget_tokens_per_goal: int = 5000,
                       n_seeds: int = 5) -> RunPlan:
    """Standard 3-arm x 5-seed x 6-goal plan (90 runs).

    Goals match the testapp's flows; happy_path_urls are the canonical
    paths the regression mode would walk on each goal. The harness uses
    them only for `off_path_fraction` on the memory arm's E-mode pass.
    """
    return RunPlan(
        release=release,
        arms=("cold", "cold_readme", "memory"),
        seeds=tuple(range(n_seeds)),
        goals=(
            GoalSpec("login", ["/login", "/session"]),
            GoalSpec("search", ["/search"]),
            GoalSpec("checkout", ["/cart", "/cart/checkout", "/checkout/continue",
                                    "/order", "/orders"]),
            GoalSpec("apply_coupon", ["/cart", "/cart/apply"]),
            GoalSpec("idempotent_order", ["/cart", "/orders"]),
            GoalSpec("admin_access", ["/login", "/settings/admin"]),
        ),
        budget_tokens_per_goal=budget_tokens_per_goal,
    )


def _build_executor_input(arm: Arm, goal: GoalSpec, plan: RunPlan,
                           base_url: str, seed: int) -> ExecutorInput:
    """What the harness hands the executor. The executor renders the
    prompt for the arm; this dict is the inputs.
    """
    return {
        "arm": arm,
        "release": plan.release,
        "seed": seed,
        "goal_id": goal.goal_id,
        "happy_path_urls": list(goal.happy_path_urls),
        "base_url": base_url,
        "budget_tokens": plan.budget_tokens_per_goal,
    }


def _build_summary_from_output(arm: Arm, seed: int, release: str,
                                 manifest: Manifest, raw: ExecutorOutput
                                 ) -> RunSummary:
    """Adapt the executor's free-form output into a typed RunSummary.

    The executor emits OBSERVATIONS in free text; the harness adjudicates
    each against the manifest (Jaccard pre-filter + LLM-judge fallback at
    the runner level, not here). Anything the manifest does not match is a
    false positive (`matched_manifest=False`); the LLM-judge is the
    decision-maker outside the deterministic pre-filter, called by the
    operator if needed.
    """
    tokens_used = int(raw.get("tokens_used", 0))
    actions_used = int(raw.get("actions_used", 0))
    off_path_fraction = raw.get("off_path_fraction")
    detections: list[Detection] = []
    for o in raw.get("observations", []):
        text = o.get("value", "") if isinstance(o, dict) else getattr(o, "value", "")
        hit = match_against_manifest(text, manifest)
        detections.append(Detection(
            arm=arm,
            seed=seed,
            slug=hit.slug if hit else None,
            observation_text=text,
            matched_manifest=hit is not None,
        ))
    return RunSummary(
        arm=arm,
        seed=seed,
        release=release,
        tokens_used=tokens_used,
        actions_used=actions_used,
        detections=detections,
        off_path_fraction=off_path_fraction,
    )


@dataclass
class RunRecord:
    """The full record of one (release, arm, seed, goal) tuple."""

    release: str
    arm: Arm
    seed: int
    goal_id: str
    raw_output: ExecutorOutput
    summary: RunSummary
    elapsed_seconds: float


def run_plan(plan: RunPlan, executor: Executor, *,
              base_url: str = "http://127.0.0.1:8000",
              manifest: Manifest | None = None,
              out_dir: Path | None = None,
              ) -> list[RunRecord]:
    """Execute one RunPlan in arm-major order (cold first, memory last).

    Order matters for honesty: the operator (or the API loop) sets up
    /_plant for the release at the start, runs all arms back-to-back, and
    only then calls /_unplant. Each arm sees the SAME plant state; memory
    is run last so any operator confusion does not let it secretly probe
    after cold has already finished and given up.
    """
    m = manifest or default_manifest()
    records: list[RunRecord] = []
    for arm in plan.arms:
        for seed in plan.seeds:
            for goal in plan.goals:
                inputs = _build_executor_input(arm, goal, plan, base_url, seed)
                t0 = time.monotonic()
                raw = executor(arm, goal.goal_id,
                                _render_prompt_for_arm(arm, goal, plan,
                                                        base_url, inputs),
                                inputs)
                elapsed = time.monotonic() - t0
                # Per-goal RunSummary; aggregate over goals belongs to the
                # per-arm step below.
                summary = _build_summary_from_output(
                    arm, seed, plan.release, m, raw,
                )
                records.append(RunRecord(
                    release=plan.release, arm=arm, seed=seed,
                    goal_id=goal.goal_id, raw_output=raw,
                    summary=summary, elapsed_seconds=elapsed,
                ))
                if out_dir:
                    _persist_record(out_dir, records[-1])
    return records


def _render_prompt_for_arm(arm: Arm, goal: GoalSpec, plan: RunPlan,
                            base_url: str, inputs: ExecutorInput) -> str:
    """The harness renders ONE-LINE arm-specific framing here; the
    runner package owns the deep prompt for `memory` (R-mode/E-mode).
    `cold` and `cold_readme` arms have no Praxis-specific prompt; they
    get the goal + the README (if any).

    For the live subscription path, this string is what the operator
    pastes into the Claude Code session for that (arm, goal). The
    operator's executor implementation is responsible for handling each
    arm's framing - the harness only documents what to give it.
    """
    if arm == "cold":
        return (
            f"Goal: {goal.goal_id}. Base URL: {base_url}. "
            f"Budget: {plan.budget_tokens_per_goal} tokens. "
            f"You have NO prior knowledge of this app. Probe it; report any "
            f"observation you can ground in HTTP-level evidence."
        )
    if arm == "cold_readme":
        return (
            f"Goal: {goal.goal_id}. Base URL: {base_url}. "
            f"Budget: {plan.budget_tokens_per_goal} tokens. "
            f"See README_FROZEN.md for what this app does and "
            f"cold_readme_per_goal.md for the goal-specific guidance. "
            f"Probe; report any deviation from the documented behavior."
        )
    # memory: the operator hands the executor the praxis runner's R-mode
    # and E-mode prompts via the praxis adapter. The harness simply
    # signals which arm this is so the executor picks the right path.
    return (
        f"Goal: {goal.goal_id}. Base URL: {base_url}. "
        f"Budget: {plan.budget_tokens_per_goal} tokens. "
        f"Arm=memory: read believed knowledge through the praxis adapter; "
        f"run R-mode followed by E-mode for this goal."
    )


def _persist_record(out_dir: Path, rec: RunRecord) -> None:
    """Persist one RunRecord to disk for post-run audit + re-aggregation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / rec.arm / str(rec.seed)
    p.mkdir(parents=True, exist_ok=True)
    payload = {
        "release": rec.release,
        "arm": rec.arm,
        "seed": rec.seed,
        "goal_id": rec.goal_id,
        "elapsed_seconds": rec.elapsed_seconds,
        "summary": asdict(rec.summary),
        "raw_output": rec.raw_output,
    }
    (p / f"{rec.goal_id}.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )


def aggregate_records(records: list[RunRecord], manifest: Manifest,
                       *, arm: Arm) -> ArmAggregate:
    """Aggregate per-goal records back to one per (arm, seed). Each seed's
    summary is the UNION of detections across goals (so seed-level recall
    is computed over ALL planted regressions for the release).
    """
    by_seed: dict[int, RunSummary] = {}
    # Collect off_path_fraction values per seed and average at the end.
    off_path_values: dict[int, list[float]] = {}
    for r in records:
        if r.arm != arm:
            continue
        s = by_seed.get(r.seed)
        if s is None:
            s = RunSummary(arm=arm, seed=r.seed, release=r.release,
                           tokens_used=0, actions_used=0,
                           detections=[], off_path_fraction=None)
            by_seed[r.seed] = s
            off_path_values[r.seed] = []
        s.tokens_used += r.summary.tokens_used
        s.actions_used += r.summary.actions_used
        s.detections.extend(r.summary.detections)
        if r.summary.off_path_fraction is not None:
            off_path_values[r.seed].append(r.summary.off_path_fraction)
    # Finalize the per-seed average for off_path_fraction.
    for seed, values in off_path_values.items():
        if values:
            by_seed[seed].off_path_fraction = sum(values) / len(values)
    return aggregate(arm, list(by_seed.values()), manifest)


def report(records: list[RunRecord], plan: RunPlan,
            manifest: Manifest | None = None,
            *, out_dir: Path | None = None,
            control_records: list[RunRecord] | None = None,
            ) -> Literal["continue", "kill"]:
    """Compute aggregates + verdict; write results.md + results.json."""
    m = manifest or default_manifest()
    arms: dict[Arm, ArmAggregate] = {
        a: aggregate_records(records, m, arm=a) for a in plan.arms
    }
    # Build control summaries for the false-pass guardrail.
    if control_records:
        control_by_arm: dict[Arm, list[RunSummary]] = {}
        for r in control_records:
            control_by_arm.setdefault(r.arm, []).append(r.summary)
        for a, ctrl_summaries in control_by_arm.items():
            if a in arms:
                # Re-aggregate with the control feed for false-pass calc.
                seed_summaries = [
                    aggregate_records(records, m, arm=a)
                    for _ in [0]
                ]
                _ = seed_summaries  # we only needed the side effect
                arms[a] = aggregate(
                    a,
                    [r.summary for r in records if r.arm == a],
                    m, control_summaries=ctrl_summaries,
                )

    verdict = evaluate(arms["memory"], arms["cold_readme"])

    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_results_markdown(
            arms, verdict,
            release=plan.release,
            budget_tokens=plan.budget_tokens_per_goal,
            path=str(out_dir / "results.md"),
        )
        (out_dir / "results.json").write_text(
            json.dumps({
                "release": plan.release,
                "arms": {a: asdict(agg) for a, agg in arms.items()},
                "verdict": verdict.verdict,
                "killed_by": list(verdict.killed_by),
            }, indent=2, default=str),
            encoding="utf-8",
        )

    return verdict.verdict
