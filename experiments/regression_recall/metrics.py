"""Regression-recall metrics + sigma-bounded kill gates.

Compute, from per-run observations vs the pre-registered manifest:
  - recall@budget (primary)
  - per-category recall (secondary)
  - false-positive rate (memory must not buy recall with false alarms)
  - false-pass rate on the unmutated control release
  - stale-trap recall (the s1_oracle_lies category-of-one)
  - off_path_fraction (E-mode degeneration floor)

Then evaluate the disjunctive kill criterion from docs/phase-1-experiment.md.
Any failing condition kills the moat claim and returns the project to the
kill/continue gate. Pre-registered sigma bounds are 2-sigma (1-sigma on the
false-positive guardrail) over inter-seed variance per arm; this module
computes a Welch approximation, not an exact test - audit-friendly and
deterministic.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Literal

from .manifest import Manifest, Regression

Arm = Literal["cold", "cold_readme", "memory"]


@dataclass(frozen=True)
class Detection:
    """One arm's recorded detection of one planted regression (or false claim).

    `slug` is None when this is a false-positive claim that does not match
    any pre-registered regression.
    """

    arm: Arm
    seed: int
    slug: str | None
    observation_text: str
    matched_manifest: bool


_PARAPHRASE_FLOOR = 0.5

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


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match_against_manifest(
    observation_text: str,
    manifest: Manifest,
) -> Regression | None:
    """Return the manifest entry this observation matches, or None."""
    best: tuple[float, Regression] | None = None
    for r in manifest.regressions:
        score = _jaccard(observation_text, r.expected_observation)
        if score >= _PARAPHRASE_FLOOR and (best is None or score > best[0]):
            best = (score, r)
    return best[1] if best else None


@dataclass
class RunSummary:
    """One arm's results from one seed on one release."""

    arm: Arm
    seed: int
    release: str
    tokens_used: int
    actions_used: int
    detections: list[Detection] = field(default_factory=list)
    off_path_fraction: float | None = None  # memory arm only

    def hit_slugs(self) -> set[str]:
        return {d.slug for d in self.detections if d.slug and d.matched_manifest}

    def false_positives(self) -> int:
        return sum(1 for d in self.detections if not d.matched_manifest)


@dataclass(frozen=True)
class ArmAggregate:
    """Per-arm aggregate over all seeds, with mean + stdev."""

    arm: Arm
    n_seeds: int
    recall_mean: float
    recall_stdev: float
    per_category_recall: dict[str, float]
    per_category_recall_stdev: dict[str, float]
    false_positive_mean: float
    false_positive_stdev: float
    false_pass_rate: float  # on unmutated control
    stale_trap_recall: float
    off_path_fraction_mean: float | None


def _safe_stdev(xs: list[float]) -> float:
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def aggregate(arm: Arm, summaries: list[RunSummary], manifest: Manifest,
               *, control_summaries: list[RunSummary] | None = None,
               stale_trap_slug: str = "s1_oracle_lies") -> ArmAggregate:
    """Aggregate per-seed RunSummaries into an ArmAggregate for one arm."""
    n_planted = len(manifest.regressions)
    per_seed_recall: list[float] = []
    per_seed_fp: list[float] = []
    per_category_seed: dict[str, list[float]] = {}
    stale_hits: list[float] = []
    off_path_vals: list[float] = []

    by_category = manifest.by_category()

    for s in summaries:
        hits = s.hit_slugs()
        per_seed_recall.append(len(hits) / n_planted if n_planted else 0.0)
        per_seed_fp.append(s.false_positives())
        for cat, rs in by_category.items():
            denom = len(rs)
            num = sum(1 for r in rs if r.slug in hits)
            per_category_seed.setdefault(cat, []).append(
                num / denom if denom else 0.0
            )
        stale_hits.append(1.0 if stale_trap_slug in hits else 0.0)
        if s.off_path_fraction is not None:
            off_path_vals.append(s.off_path_fraction)

    false_pass = 0.0
    if control_summaries:
        # False-pass = control release (no planted regressions) but arm emitted
        # a "regression detected" observation. Each such observation is one
        # event in the rate denominator (seeds).
        n_ctrl = len(control_summaries)
        if n_ctrl:
            false_pass = sum(
                1 for s in control_summaries if s.detections
            ) / n_ctrl

    return ArmAggregate(
        arm=arm,
        n_seeds=len(summaries),
        recall_mean=statistics.fmean(per_seed_recall) if per_seed_recall else 0.0,
        recall_stdev=_safe_stdev(per_seed_recall),
        per_category_recall={
            c: statistics.fmean(v) if v else 0.0
            for c, v in per_category_seed.items()
        },
        per_category_recall_stdev={
            c: _safe_stdev(v) for c, v in per_category_seed.items()
        },
        false_positive_mean=statistics.fmean(per_seed_fp) if per_seed_fp else 0.0,
        false_positive_stdev=_safe_stdev(per_seed_fp),
        false_pass_rate=false_pass,
        stale_trap_recall=statistics.fmean(stale_hits) if stale_hits else 0.0,
        off_path_fraction_mean=(
            statistics.fmean(off_path_vals) if off_path_vals else None
        ),
    )


def _welch_sigma(a_stdev: float, n_a: int, b_stdev: float, n_b: int) -> float:
    """Pooled stdev for a 2-arm comparison (Welch-style approximation)."""
    if n_a <= 0 or n_b <= 0:
        return 0.0
    return math.sqrt((a_stdev**2) / n_a + (b_stdev**2) / n_b)


@dataclass(frozen=True)
class KillGate:
    """One pre-registered kill criterion + its evaluation."""

    name: str
    description: str
    passed: bool  # True = the moat survives this gate
    detail: str


@dataclass(frozen=True)
class Verdict:
    """The Phase 1 verdict from the regression-recall experiment."""

    gates: tuple[KillGate, ...]
    verdict: Literal["continue", "kill"]

    @property
    def killed_by(self) -> tuple[str, ...]:
        return tuple(g.name for g in self.gates if not g.passed)


def evaluate(memory: ArmAggregate, cold_readme: ArmAggregate,
              *, knowledge_visible_categories: tuple[str, ...] =
                  ("knowledge_visible",),
              sigma_floor_recall: float = 2.0,
              sigma_floor_fp: float = 1.0,
              min_overall_gap: float = 0.15,
              min_category_gap: float = 0.25,
              max_fp_excess: float = 0.05,
              min_stale_trap_recall: float = 0.5,
              min_off_path_fraction: float = 0.4,
              ) -> Verdict:
    """Apply the pre-registered kill criteria (docs/phase-1-experiment.md).

    Returns a Verdict with one KillGate per criterion. Any failing gate flips
    the verdict to `kill`. Bounds are 2-sigma for recall, 1-sigma for the
    false-positive guardrail (asymmetric: an arm buying recall with false
    alarms is a more visible failure than a tied recall).
    """
    gates: list[KillGate] = []

    # 1. Overall recall edge with 2-sigma bound.
    delta = memory.recall_mean - cold_readme.recall_mean
    sigma = _welch_sigma(memory.recall_stdev, memory.n_seeds,
                          cold_readme.recall_stdev, cold_readme.n_seeds)
    g1_passed = (delta >= min_overall_gap) and (
        sigma == 0.0 or delta >= sigma_floor_recall * sigma
    )
    gates.append(KillGate(
        name="overall_recall",
        description=(
            f"recall(memory) - recall(cold_readme) >= {min_overall_gap} "
            f"AND >= {sigma_floor_recall} sigma"
        ),
        passed=g1_passed,
        detail=(
            f"delta={delta:.3f}, sigma={sigma:.3f}, "
            f"memory.recall={memory.recall_mean:.3f}+/-{memory.recall_stdev:.3f}, "
            f"cold_readme.recall={cold_readme.recall_mean:.3f}+/-{cold_readme.recall_stdev:.3f}"
        ),
    ))

    # 2. Knowledge-visible recall gap with 2-sigma bound.
    def _cat_recall(agg: ArmAggregate) -> tuple[float, float]:
        # Compose the "combined" knowledge-visible recall as the
        # equally-weighted mean of the included categories.
        means = [agg.per_category_recall.get(c, 0.0)
                 for c in knowledge_visible_categories]
        stdevs = [agg.per_category_recall_stdev.get(c, 0.0)
                  for c in knowledge_visible_categories]
        m = statistics.fmean(means) if means else 0.0
        # Composite stdev via root-mean-square (independent per-category).
        s = math.sqrt(sum(x * x for x in stdevs) / len(stdevs)) if stdevs else 0.0
        return m, s

    m_mean, m_std = _cat_recall(memory)
    c_mean, c_std = _cat_recall(cold_readme)
    cat_delta = m_mean - c_mean
    cat_sigma = _welch_sigma(m_std, memory.n_seeds, c_std, cold_readme.n_seeds)
    g2_passed = (cat_delta >= min_category_gap) and (
        cat_sigma == 0.0 or cat_delta >= sigma_floor_recall * cat_sigma
    )
    gates.append(KillGate(
        name="knowledge_visible_recall",
        description=(
            f"knowledge-visible recall(memory) - recall(cold_readme) >= "
            f"{min_category_gap} AND >= {sigma_floor_recall} sigma"
        ),
        passed=g2_passed,
        detail=(
            f"delta={cat_delta:.3f}, sigma={cat_sigma:.3f}, "
            f"memory={m_mean:.3f}+/-{m_std:.3f}, "
            f"cold_readme={c_mean:.3f}+/-{c_std:.3f}, "
            f"categories={list(knowledge_visible_categories)}"
        ),
    ))

    # 3. False-positive guardrail (memory must not buy recall with false alarms).
    fp_delta = memory.false_positive_mean - cold_readme.false_positive_mean
    fp_sigma = _welch_sigma(memory.false_positive_stdev, memory.n_seeds,
                             cold_readme.false_positive_stdev, cold_readme.n_seeds)
    # The gate FAILS when memory's excess exceeds the threshold + 1 sigma.
    g3_passed = not (
        fp_delta > max_fp_excess
        and (fp_sigma == 0.0 or fp_delta >= sigma_floor_fp * fp_sigma)
    )
    gates.append(KillGate(
        name="false_positive_guardrail",
        description=(
            f"false_positive(memory) - false_positive(cold_readme) <= "
            f"{max_fp_excess} OR within {sigma_floor_fp} sigma"
        ),
        passed=g3_passed,
        detail=(
            f"fp_delta={fp_delta:.3f}, sigma={fp_sigma:.3f}, "
            f"memory_fp={memory.false_positive_mean:.3f}+/-{memory.false_positive_stdev:.3f}"
        ),
    ))

    # 4. False-pass on the unmutated control release.
    g4_passed = memory.false_pass_rate == 0.0
    gates.append(KillGate(
        name="false_pass_control",
        description="memory must NOT report regressions on a clean release",
        passed=g4_passed,
        detail=f"false_pass_rate={memory.false_pass_rate:.3f}",
    ))

    # 5. Stale-trap recall.
    g5_passed = memory.stale_trap_recall >= min_stale_trap_recall
    gates.append(KillGate(
        name="stale_trap_recall",
        description=(
            f"memory must catch the stale-trap regression in >= "
            f"{min_stale_trap_recall:.0%} of seeds"
        ),
        passed=g5_passed,
        detail=f"stale_trap_recall={memory.stale_trap_recall:.3f}",
    ))

    # 6. E-mode off-path fraction floor.
    if memory.off_path_fraction_mean is None:
        # No E-mode runs in this report. Defer to harness, do not silently pass.
        gates.append(KillGate(
            name="off_path_fraction",
            description=f"E-mode off_path_fraction >= {min_off_path_fraction}",
            passed=False,
            detail="off_path_fraction unrecorded (E-mode runs missing)",
        ))
    else:
        g6_passed = memory.off_path_fraction_mean >= min_off_path_fraction
        gates.append(KillGate(
            name="off_path_fraction",
            description=f"E-mode off_path_fraction >= {min_off_path_fraction}",
            passed=g6_passed,
            detail=f"off_path_fraction_mean={memory.off_path_fraction_mean:.3f}",
        ))

    verdict: Literal["continue", "kill"] = (
        "continue" if all(g.passed for g in gates) else "kill"
    )
    return Verdict(gates=tuple(gates), verdict=verdict)


def write_results_markdown(
    arms: dict[Arm, ArmAggregate],
    verdict: Verdict,
    *,
    release: str,
    budget_tokens: int,
    path: str,
) -> None:
    """Render the experiment report. One file per release."""
    from pathlib import Path
    lines: list[str] = [
        f"# Phase 1 regression-recall results - release {release}",
        "",
        f"Budget per arm per goal: **{budget_tokens} tokens**",
        "",
        "## Arm aggregates",
        "",
        "| arm | n_seeds | recall | knowledge-visible | stale-trap | false_pos | off_path |",
        "|-----|---------|--------|--------------------|------------|-----------|----------|",
    ]
    for arm_name, agg in arms.items():
        kv = agg.per_category_recall.get("knowledge_visible", 0.0)
        kv_s = agg.per_category_recall_stdev.get("knowledge_visible", 0.0)
        off = (f"{agg.off_path_fraction_mean:.2f}"
               if agg.off_path_fraction_mean is not None else "-")
        lines.append(
            f"| `{arm_name}` | {agg.n_seeds} | "
            f"{agg.recall_mean:.2f}+/-{agg.recall_stdev:.2f} | "
            f"{kv:.2f}+/-{kv_s:.2f} | {agg.stale_trap_recall:.2f} | "
            f"{agg.false_positive_mean:.2f}+/-{agg.false_positive_stdev:.2f} | "
            f"{off} |"
        )
    lines += [
        "",
        "## Kill gates",
        "",
        "| gate | passed | detail |",
        "|------|--------|--------|",
    ]
    for g in verdict.gates:
        mark = "PASS" if g.passed else "**FAIL**"
        lines.append(f"| `{g.name}` | {mark} | {g.detail} |")
    lines += [
        "",
        f"## Verdict: **{verdict.verdict.upper()}**",
        "",
    ]
    if verdict.verdict == "kill":
        lines.append("Killed by: " + ", ".join(verdict.killed_by))
    else:
        lines.append("All gates passed; the moat survives this experiment run.")
        lines.append("Phase 1 continues; ADR-0010 records the verdict.")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
