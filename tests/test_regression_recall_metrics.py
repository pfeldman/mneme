"""Regression-recall metrics + kill-gate tests.

The metrics are the Phase 1 falsification core. Tests pin:
  - manifest loading + integrity check (unique slugs).
  - Jaccard match against expected_observation tolerates paraphrase.
  - per-arm aggregation produces mean + stdev correctly.
  - each kill gate fires when its condition is breached, passes otherwise.
  - the verdict is `kill` if ANY gate fails (disjunction).
"""
from __future__ import annotations

from pathlib import Path

import pytest

# conftest.py puts experiments/ on sys.path so the package is importable.
from regression_recall.manifest import default_manifest, load_manifest
from regression_recall.metrics import (
    Detection,
    RunSummary,
    aggregate,
    evaluate,
    match_against_manifest,
    write_results_markdown,
)


def test_default_manifest_loads_8_regressions() -> None:
    m = default_manifest()
    assert len(m.regressions) == 8
    assert len(m.by_slug()) == 8  # no duplicates


def test_manifest_by_category() -> None:
    m = default_manifest()
    cats = m.by_category()
    assert len(cats["tourist"]) == 2
    assert len(cats["knowledge_visible"]) == 5
    assert len(cats["stale_trap"]) == 1


def test_load_manifest_rejects_duplicate_slugs(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"schema_version":"1","release":"x","testapp_baseline_sha":"y",'
        '"regressions":['
        '{"slug":"r","category":"tourist","description":"d","goal_id":"g",'
        '"plant_endpoint":"/p","expected_observation":"o",'
        '"expected_kind":"failure","expected_signal_type":"text"},'
        '{"slug":"r","category":"tourist","description":"d","goal_id":"g",'
        '"plant_endpoint":"/p","expected_observation":"o",'
        '"expected_kind":"failure","expected_signal_type":"text"}]}'
    )
    with pytest.raises(ValueError):
        load_manifest(bad)


def test_match_against_manifest_paraphrase() -> None:
    m = default_manifest()
    # Realistic agent paraphrase (it observed the HTTP call and the response):
    hit = match_against_manifest(
        "POST /cart/apply with coupon SAVE10 at subtotal 49 returned 200 with applied=true",
        m,
    )
    assert hit is not None and hit.slug == "k1_save10_at_49"


def test_match_against_manifest_no_match_for_unrelated() -> None:
    m = default_manifest()
    miss = match_against_manifest("the cat sat on the mat", m)
    assert miss is None


def _summaries_for(arm: str, recalls: list[set[str]], false_positives: int = 0,
                    off_path: float | None = None) -> list[RunSummary]:
    """Build per-seed RunSummary list from hit-slug-sets per seed."""
    out: list[RunSummary] = []
    for seed, hits in enumerate(recalls):
        detections: list[Detection] = []
        for s in hits:
            detections.append(Detection(
                arm=arm,  # type: ignore[arg-type]
                seed=seed,
                slug=s,
                observation_text="x",
                matched_manifest=True,
            ))
        for _ in range(false_positives):
            detections.append(Detection(
                arm=arm,  # type: ignore[arg-type]
                seed=seed, slug=None, observation_text="fp",
                matched_manifest=False,
            ))
        out.append(RunSummary(
            arm=arm,  # type: ignore[arg-type]
            seed=seed, release="phase-1-r1",
            tokens_used=1000, actions_used=10,
            detections=detections,
            off_path_fraction=off_path,
        ))
    return out


def test_aggregate_computes_recall_mean_and_stdev() -> None:
    m = default_manifest()
    # 5 seeds for memory: hits all 8 in seeds 0/1, 7 of 8 in seeds 2/3/4.
    memory_summaries = _summaries_for(
        "memory",
        [
            {r.slug for r in m.regressions},  # 8/8
            {r.slug for r in m.regressions},
            {r.slug for r in m.regressions if r.slug != "k5_filter_lost"},  # 7/8
            {r.slug for r in m.regressions if r.slug != "k5_filter_lost"},
            {r.slug for r in m.regressions if r.slug != "k5_filter_lost"},
        ],
        off_path=0.55,
    )
    agg = aggregate("memory", memory_summaries, m)
    assert agg.n_seeds == 5
    # 2 seeds hit 8/8, 3 seeds hit 7/8 -> mean = (2*1 + 3*7/8) / 5
    expected = (2 * 1.0 + 3 * 7 / 8) / 5
    assert agg.recall_mean == pytest.approx(expected)
    assert agg.recall_stdev > 0
    assert agg.per_category_recall["knowledge_visible"] > 0
    assert agg.off_path_fraction_mean == 0.55


def test_evaluate_passes_when_memory_clearly_better() -> None:
    m = default_manifest()
    # Memory finds 7/8 reliably; cold_readme finds 2/8.
    memory = _summaries_for(
        "memory",
        [
            {r.slug for r in m.regressions if r.slug != "k5_filter_lost"}
            for _ in range(5)
        ],
        off_path=0.55,
    )
    cold = _summaries_for(
        "cold_readme",
        [{"t1_login_500", "t2_search_blank"} for _ in range(5)],
    )
    agg_m = aggregate("memory", memory, m)
    agg_c = aggregate("cold_readme", cold, m)
    v = evaluate(agg_m, agg_c)
    # With this gap, overall + knowledge-visible + stale-trap should all pass.
    # (memory caught s1_oracle_lies in every seed)
    assert v.verdict == "continue", v.killed_by


def test_evaluate_kills_when_recall_gap_too_small() -> None:
    m = default_manifest()
    # Memory ties cold; should kill.
    memory = _summaries_for(
        "memory",
        [{"t1_login_500"} for _ in range(5)],
        off_path=0.7,
    )
    cold = _summaries_for(
        "cold_readme",
        [{"t1_login_500"} for _ in range(5)],
    )
    v = evaluate(aggregate("memory", memory, m), aggregate("cold_readme", cold, m))
    assert v.verdict == "kill"
    assert "overall_recall" in v.killed_by


def test_evaluate_kills_on_false_positive_excess() -> None:
    m = default_manifest()
    memory = _summaries_for(
        "memory",
        [{r.slug for r in m.regressions if r.slug != "k5_filter_lost"}
         for _ in range(5)],
        false_positives=4,  # heavy false-alarm rate
        off_path=0.55,
    )
    cold = _summaries_for(
        "cold_readme",
        [{"t1_login_500"} for _ in range(5)],
        false_positives=0,
    )
    v = evaluate(aggregate("memory", memory, m), aggregate("cold_readme", cold, m))
    assert "false_positive_guardrail" in v.killed_by


def test_evaluate_kills_on_emode_collapse() -> None:
    m = default_manifest()
    memory = _summaries_for(
        "memory",
        [{r.slug for r in m.regressions if r.slug != "k5_filter_lost"}
         for _ in range(5)],
        off_path=0.1,  # E-mode collapsed into R-mode
    )
    cold = _summaries_for("cold_readme",
                          [{"t1_login_500"} for _ in range(5)])
    v = evaluate(aggregate("memory", memory, m), aggregate("cold_readme", cold, m))
    assert "off_path_fraction" in v.killed_by


def test_evaluate_kills_on_stale_trap_miss() -> None:
    m = default_manifest()
    # Memory catches everything EXCEPT the stale-trap (the s1_oracle_lies case).
    memory = _summaries_for(
        "memory",
        [
            {r.slug for r in m.regressions if r.slug != "s1_oracle_lies"}
            for _ in range(5)
        ],
        off_path=0.55,
    )
    cold = _summaries_for("cold_readme",
                          [{"t1_login_500"} for _ in range(5)])
    v = evaluate(aggregate("memory", memory, m), aggregate("cold_readme", cold, m))
    assert "stale_trap_recall" in v.killed_by


def test_evaluate_kills_on_false_pass_on_control() -> None:
    m = default_manifest()
    memory_runs = _summaries_for(
        "memory",
        [{r.slug for r in m.regressions if r.slug != "k5_filter_lost"}
         for _ in range(5)],
        off_path=0.55,
    )
    # Control release (no regressions planted); memory reports detections anyway.
    control_summary = RunSummary(
        arm="memory", seed=0, release="control",
        tokens_used=1000, actions_used=10,
        detections=[Detection(arm="memory", seed=0, slug=None,
                              observation_text="false positive",
                              matched_manifest=False)],
        off_path_fraction=0.55,
    )
    agg_m = aggregate("memory", memory_runs, m, control_summaries=[control_summary])
    cold = _summaries_for("cold_readme",
                          [{"t1_login_500"} for _ in range(5)])
    v = evaluate(agg_m, aggregate("cold_readme", cold, m))
    assert "false_pass_control" in v.killed_by


def test_write_results_markdown(tmp_path: Path) -> None:
    m = default_manifest()
    memory = _summaries_for("memory",
                            [{r.slug for r in m.regressions} for _ in range(3)],
                            off_path=0.6)
    cold_readme = _summaries_for("cold_readme",
                                 [{"t1_login_500"} for _ in range(3)])
    cold = _summaries_for("cold", [set() for _ in range(3)])
    arms = {
        "memory": aggregate("memory", memory, m),
        "cold_readme": aggregate("cold_readme", cold_readme, m),
        "cold": aggregate("cold", cold, m),
    }
    v = evaluate(arms["memory"], arms["cold_readme"])
    out = tmp_path / "results.md"
    write_results_markdown(arms, v, release="r1", budget_tokens=5000, path=str(out))
    text = out.read_text()
    assert "regression-recall results" in text
    assert "release r1" in text
    assert "Kill gates" in text
    assert "memory" in text
    assert "cold_readme" in text
