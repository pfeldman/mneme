"""Maintenance-over-time (the MBT-revival test). Pins the two things that decide the
bet (docs/06): the model never drifts to believed-garbage as the app evolves, and a
human is needed ONLY for genuine semantic changes — cosmetic/implementation changes
self-heal. See experiments/ui-mutation/evolution.py."""
from __future__ import annotations

import importlib

evolution = importlib.import_module("evolution")


def test_model_never_drifts_to_garbage_and_stays_correct() -> None:
    r = evolution.run()
    assert r["silent_drift_events"] == 0      # a wrong signal must NEVER stay believed
    assert r["worst_residual_drift"] == 0
    assert r["all_versions_correct_after_maintenance"] is True
    assert r["PASSED"] is True


def test_humans_needed_only_for_semantic_changes() -> None:
    r = evolution.run()
    # 4 versions: cosmetic + implementation self-heal (0 interventions); only the
    # genuine "what success MEANS" change requires a human re-seed.
    assert r["human_interventions"] == 1
    log = {row["version"]: row for row in r["log"]}
    assert log["2026.2"]["intervened"] is False   # cosmetic redesign
    assert log["2026.3"]["intervened"] is False   # implementation change
    assert log["2026.4"]["intervened"] is True    # semantic change (MFA)
