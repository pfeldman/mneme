"""The Phase-0 experiment must run end-to-end and apply the gates IN ORDER. These
assert the MACHINERY (gates, metrics, oracle wiring, mutation flow), not the thesis
— the numbers come from the deterministic `simapp` stand-in (see its module docstring
and the experiment README)."""
from __future__ import annotations

import importlib

harness = importlib.import_module("harness")
metrics = importlib.import_module("metrics")
mutate = importlib.import_module("mutate")
simapp = importlib.import_module("simapp")


def test_experiment_runs_and_reports_all_three_gates() -> None:
    result = harness.run_experiment()
    assert "existential_gate" in result
    assert "robustness_gate" in result
    assert "oracle_error_rates" in result
    assert result["verdict"].startswith("CONTINUE")


def test_existential_gate_memory_cheaper_at_equal_reliability() -> None:
    result = harness.run_experiment()
    g = result["existential_gate"]
    assert g["memory_avg_tokens"] < g["cold_avg_tokens"]
    assert g["memory_success"] >= g["cold_success"]
    assert g["PASSED"]


def test_robustness_memory_beats_recorded_script() -> None:
    result = harness.run_experiment()
    g = result["robustness_gate"]
    assert g["memory_recovery"] > g["recorded_recovery"]


def test_oracle_does_not_false_pass_when_app_is_broken() -> None:
    # The sabotage runs (app silently broken) must never make the oracle claim
    # success — that is the failure mode that makes memory worse than nothing.
    result = harness.run_experiment()
    assert result["oracle_error_rates"]["false_pass"] == 0.0


def test_recorded_script_breaks_on_every_applicable_mutation() -> None:
    for m in mutate.Mutation:
        mutate.apply(m)
        try:
            for flow in ("login", "search", "checkout"):
                if not simapp.mutation_changes_flow(flow, m):
                    continue
                assert simapp.run_recorded(flow).succeeded is False
                assert simapp.run_memory(flow).succeeded is True
        finally:
            mutate.reset()


def test_gate_order_short_circuits_on_m1_failure() -> None:
    # If M1 fails, the verdict stops there and never reports M2 (docs/04 ordering).
    g1_fail = {"PASSED": False}
    v = metrics.verdict(g1_fail, {"PASSED": True}, {"false_pass": 0.0})
    assert v.startswith("STOP") and "M1" in v
