"""Regression-recall harness tests (offline; fake executor).

Cover the harness loop end-to-end without the subscription / API path:
  - executor protocol is honored (arm/seed/goal tuple gets the right prompt).
  - per-goal RunRecords aggregate up to per-(arm, seed) summaries that the
    metrics layer consumes.
  - verdict propagates from `evaluate` correctly.
  - results.md + results.json are written on `report(...)` when out_dir given.
"""
from __future__ import annotations

import json
from pathlib import Path

from regression_recall.harness import (
    GoalSpec,
    RunPlan,
    aggregate_records,
    build_default_plan,
    report,
    run_plan,
)
from regression_recall.manifest import default_manifest


def _strong_memory_executor(arm, goal_id, prompt, inputs):
    """A fake executor where memory finds all 8 regressions reliably and
    spends most actions off the happy path; cold_readme finds one
    tourist regression; cold finds none.

    The fake emits observations whose text overlaps strongly with the
    manifest's expected_observation so the deterministic Jaccard matcher
    scores them as hits. Updated post-dry-run: s1 detection routes
    through /me (not "follow-up authenticated request fails") to match
    the manifest change documented in ADR-0009.
    """
    if arm == "memory":
        if goal_id == "login":
            obs = [
                {"value": "POST /session returns 500 (server error on login)"},
                {"value": "GET /settings/admin returns 200 admin content for non-admin"},
                {"value": "GET /me with the just-issued session cookie returns 401 unauthorized even though POST /session returned 200 with the cookie"},
            ]
            visited = ["/login", "/session", "/me", "/settings/admin", "/_state"]
        elif goal_id == "search":
            obs = [
                {"value": "search results list is empty (no items rendered)"},
                {"value": "GET /list page=2 with filter applied returns items without the filter"},
            ]
            visited = ["/search", "/list", "/list?page=2"]
        elif goal_id == "checkout":
            obs = [
                {"value": "POST /cart/apply with coupon SAVE10 and subtotal 49 returns 200 applied=true"},
                {"value": "POST /cart/apply accepts a second coupon while the first is still active"},
                {"value": "POST /orders with the same Idempotency-Key returns two distinct order_ids"},
            ]
            visited = ["/cart", "/cart/apply", "/orders"]
        else:
            obs, visited = [], []
        return {
            "observations": obs,
            "actions_used": 5,
            "tokens_used": 1000,
            "off_path_fraction": 0.6,
            "visited_urls": visited,
        }
    if arm == "cold_readme":
        if goal_id == "login":
            obs = [{"value": "POST /session returns 500 (server error on login)"}]
        elif goal_id == "search":
            obs = [{"value": "search results list is empty (no items rendered)"}]
        else:
            obs = []
        return {
            "observations": obs,
            "actions_used": 4,
            "tokens_used": 800,
            "off_path_fraction": None,
            "visited_urls": [],
        }
    # cold
    return {
        "observations": [],
        "actions_used": 3,
        "tokens_used": 600,
        "off_path_fraction": None,
        "visited_urls": [],
    }


def test_run_plan_iterates_arm_seed_goal() -> None:
    plan = RunPlan(
        release="x", arms=("cold", "memory"), seeds=(0, 1),
        goals=(GoalSpec("login"), GoalSpec("search")),
        budget_tokens_per_goal=100,
    )
    seen: list[tuple[str, int, str]] = []

    def exe(arm, goal_id, prompt, inputs):
        seen.append((arm, inputs["seed"], goal_id))
        assert inputs["budget_tokens"] == 100
        return {"observations": [], "actions_used": 1, "tokens_used": 10,
                "off_path_fraction": None, "visited_urls": []}

    recs = run_plan(plan, exe, base_url="http://x")
    assert len(recs) == 8  # 2 arms * 2 seeds * 2 goals
    # arm-major: cold first, then memory
    assert [s[0] for s in seen] == ["cold"] * 4 + ["memory"] * 4


def test_run_plan_renders_arm_specific_prompt() -> None:
    plan = RunPlan(
        release="x", arms=("cold", "cold_readme", "memory"), seeds=(0,),
        goals=(GoalSpec("login"),),
        budget_tokens_per_goal=42,
    )
    prompts: dict[str, str] = {}

    def exe(arm, goal_id, prompt, inputs):
        prompts[arm] = prompt
        return {"observations": [], "actions_used": 0, "tokens_used": 0,
                "off_path_fraction": None, "visited_urls": []}

    run_plan(plan, exe, base_url="http://x")
    assert "NO prior knowledge" in prompts["cold"]
    assert "README_FROZEN.md" in prompts["cold_readme"]
    assert "praxis adapter" in prompts["memory"]
    assert "42" in prompts["cold"]  # budget threads through


def test_run_plan_verdict_continue_when_memory_dominates(tmp_path: Path) -> None:
    plan = build_default_plan(release="phase-1-r1", n_seeds=5,
                               budget_tokens_per_goal=1000)
    records = run_plan(plan, _strong_memory_executor,
                       base_url="http://x", out_dir=tmp_path / "runs")
    # Per-goal records: 3 arms x 5 seeds x 3 goals = 45 (the default plan
    # consolidated to 3 goals after the Phase-1 dry-run revealed the 6-
    # goal split padded the experiment with empty goals).
    assert len(records) == 45
    verdict = report(records, plan, out_dir=tmp_path)
    # The fake executor is rigged so memory wins. Verdict must continue.
    assert verdict == "continue"
    md = (tmp_path / "results.md").read_text()
    assert "Verdict" in md and "CONTINUE" in md
    js = json.loads((tmp_path / "results.json").read_text())
    assert js["verdict"] == "continue"
    assert "memory" in js["arms"] and "cold_readme" in js["arms"]
    # Per-record files were also written:
    assert (tmp_path / "runs" / "memory" / "0" / "login.json").exists()


def test_run_plan_verdict_kill_when_memory_ties_cold(tmp_path: Path) -> None:
    plan = build_default_plan(release="phase-1-r1", n_seeds=3,
                               budget_tokens_per_goal=1000)

    def tied(arm, goal_id, prompt, inputs):
        # All arms find the same 1 tourist regression on login; tied recall.
        if goal_id == "login":
            return {
                "observations": [
                    {"value": "POST /session returns 500 (server error on login)"},
                ],
                "actions_used": 1, "tokens_used": 100,
                "off_path_fraction": 0.6 if arm == "memory" else None,
                "visited_urls": [],
            }
        return {"observations": [], "actions_used": 1, "tokens_used": 100,
                "off_path_fraction": 0.6 if arm == "memory" else None,
                "visited_urls": []}

    records = run_plan(tied, exec_arg if False else tied,  # type: ignore  # noqa
                       base_url="http://x") if False else run_plan(plan, tied,
                                                                   base_url="http://x")
    verdict = report(records, plan)
    assert verdict == "kill"


def test_aggregate_records_unions_detections_per_seed() -> None:
    plan = build_default_plan(release="x", n_seeds=2, budget_tokens_per_goal=500)
    records = run_plan(plan, _strong_memory_executor, base_url="http://x")
    m = default_manifest()
    agg = aggregate_records(records, m, arm="memory")
    # Each seed's memory arm collects detections from all 3 goals.
    # Expected hits per seed (from _strong_memory_executor):
    #   login -> t1_login_500, k4_admin_settings, s1_oracle_lies
    #   search -> t2_search_blank, k5_filter_lost
    #   checkout -> k1_save10_at_49, k2_stack_codes, k3_double_order
    # Total = 8/8 of the manifest's regressions.
    assert agg.recall_mean == 1.0
    # Off-path fraction averaged across goals per seed.
    assert agg.off_path_fraction_mean == 0.6
