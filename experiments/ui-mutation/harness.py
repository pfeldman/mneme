"""Experiment orchestrator. Runs the three arms and applies the gates IN ORDER:
the existential gate (memory vs cold agent) is checked FIRST and short-circuits.

Usage:  python experiments/ui-mutation/harness.py
        (or: python -m pytest tests/test_experiment_harness.py)

IMPORTANT HONESTY NOTE: with no Browser Use runtime / LLM / live SUT available,
this runs against `simapp`, a deterministic stand-in whose token costs are stated
ASSUMPTIONS, not measurements. It validates the harness, metrics, oracle wiring and
the kill/continue logic end-to-end. It does NOT, by itself, validate the thesis.
Swap `simapp` for the live Browser Use adapter to get empirical numbers.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `mneme` importable and allow running as a plain script (the package dir name
# `ui-mutation` is not a valid module path, so we bootstrap sys.path here).
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parents[1] / "src"
for _p in (str(_SRC), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from . import metrics as M
    from . import mutate
    from . import runtimes as R
    from . import simapp
except ImportError:  # plain-script execution
    import metrics as M  # type: ignore[no-redef]
    import mutate  # type: ignore[no-redef]
    import runtimes as R  # type: ignore[no-redef]
    import simapp  # type: ignore[no-redef]

from mneme.store import FileEventStore

FLOWS = ["login", "search", "checkout"]
MEASURE_REPS = 5  # deterministic sim; reps give the rates/averages real denominators

# Configured by main(); run_arm() reads them (keeps the scaffold's run_arm signature).
_ADAPTER: R.BrowserUseAdapter | None = None
_GOAL_IDS: dict[str, str] = {}


def run_arm(arm: str, mutated: bool) -> list[M.RunResult]:
    """Execute `arm` over FLOWS and return RunResults.
    arm in {"memory","cold_agent","recorded_script"}. Oracles are seeded before the
    memory arm explores (done in main(), ADR-0005)."""
    assert _ADAPTER is not None, "call main()/setup() first"
    results: list[M.RunResult] = []
    for flow in FLOWS:
        goal_id = _GOAL_IDS[flow]
        for _ in range(MEASURE_REPS):
            if arm == "memory":
                kf = _ADAPTER.read_knowledge(goal_id)
                assert kf is not None, f"no believed knowledge for {goal_id}"
                outcome = simapp.run_memory(flow)
                oracle = R.oracle_said_success(kf, outcome.observed)
                results.append(M.RunResult(
                    arm, flow, mutated, succeeded=outcome.succeeded, tokens=outcome.tokens,
                    wall_seconds=_wall(outcome.tokens),
                    oracle_said_success=oracle, ground_truth_success=outcome.succeeded,
                ))
            elif arm == "cold_agent":
                outcome = simapp.run_cold(flow)
                results.append(M.RunResult(
                    arm, flow, mutated, succeeded=outcome.succeeded, tokens=outcome.tokens,
                    wall_seconds=_wall(outcome.tokens),
                ))
            elif arm == "recorded_script":
                outcome = simapp.run_recorded(flow)
                results.append(M.RunResult(
                    arm, flow, mutated, succeeded=outcome.succeeded, tokens=outcome.tokens,
                    wall_seconds=_wall(outcome.tokens),
                ))
            else:  # pragma: no cover - defensive
                raise ValueError(f"unknown arm: {arm}")
    return results


def run_flow(arm: str, flow: str, mutated: bool) -> list[M.RunResult]:
    """Run one arm against one flow for MEASURE_REPS (used by the robustness loop so
    it can skip mutations that do not perturb a given flow)."""
    assert _ADAPTER is not None
    goal_id = _GOAL_IDS[flow]
    results: list[M.RunResult] = []
    for _ in range(MEASURE_REPS):
        if arm == "memory":
            kf = _ADAPTER.read_knowledge(goal_id)
            assert kf is not None
            outcome = simapp.run_memory(flow)
            results.append(M.RunResult(
                arm, flow, mutated, succeeded=outcome.succeeded, tokens=outcome.tokens,
                wall_seconds=_wall(outcome.tokens),
                oracle_said_success=R.oracle_said_success(kf, outcome.observed),
                ground_truth_success=outcome.succeeded,
            ))
        elif arm == "recorded_script":
            outcome = simapp.run_recorded(flow)
            results.append(M.RunResult(
                arm, flow, mutated, succeeded=outcome.succeeded, tokens=outcome.tokens,
                wall_seconds=_wall(outcome.tokens),
            ))
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported arm for run_flow: {arm}")
    return results


def _wall(tokens: int) -> float:
    """Wall-time PROXY (sim has no real clock): token-proportional. Replace with a
    real stopwatch in the live runtime."""
    return round(tokens / 1500.0, 3)


def guardrail_runs() -> list[M.RunResult]:
    """Sabotage runs where the app silently regressed (terminal signals never
    appear). They exist to prove the shared oracle does NOT lie (false-pass) when
    the app is broken — the failure mode that makes memory worse than nothing."""
    assert _ADAPTER is not None
    out: list[M.RunResult] = []
    for flow in FLOWS:
        goal_id = _GOAL_IDS[flow]
        kf = _ADAPTER.read_knowledge(goal_id)
        assert kf is not None
        outcome = simapp.run_memory(flow, app_broken=True)
        oracle = R.oracle_said_success(kf, outcome.observed)
        out.append(M.RunResult(
            "memory", flow, mutated=False, succeeded=outcome.succeeded, tokens=outcome.tokens,
            wall_seconds=_wall(outcome.tokens),
            oracle_said_success=oracle, ground_truth_success=outcome.succeeded,
        ))
    return out


def setup() -> None:
    """Fresh store, seed every goal's oracle, then populate believed knowledge by
    exploring the unmutated flows (ADR-0005: seed first, then explore)."""
    global _ADAPTER, _GOAL_IDS
    import tempfile

    store = FileEventStore(tempfile.mkdtemp(prefix="mneme-exp-"))
    _ADAPTER, _GOAL_IDS = R.make_adapter(store, app="acme-web")
    mutate.reset()
    for flow in FLOWS:
        # A couple of exploration runs so agent evidence (behavioral+network) lands.
        R.explore_and_write(_ADAPTER, flow, _GOAL_IDS[flow])
        R.explore_and_write(_ADAPTER, flow, _GOAL_IDS[flow])


def run_experiment() -> dict:
    """Run the full protocol and return the structured verdict (also used by tests)."""
    setup()

    # 1) EXISTENTIAL GATE first (no mutation): if cold wins/ties on cost, stop.
    memory = run_arm("memory", mutated=False)
    cold = run_arm("cold_agent", mutated=False)
    gate1 = M.existential_gate(memory, cold)

    result: dict = {"existential_gate": gate1}
    if not gate1["PASSED"]:
        result["verdict"] = "STOP — no cost/reliability edge over a cold agent."
        M.write_report(memory + cold)
        return result

    # 2) ROBUSTNESS: apply mutations one at a time, memory vs recorded script.
    mutated_memory: list[M.RunResult] = []
    recorded: list[M.RunResult] = []
    for m in mutate.Mutation:
        mutate.apply(m)
        for flow in FLOWS:
            # Only measure robustness where the mutation actually perturbs the flow.
            if not simapp.mutation_changes_flow(flow, m):
                continue
            mutated_memory += run_flow("memory", flow, mutated=True)
            recorded += run_flow("recorded_script", flow, mutated=True)
        mutate.reset()
    gate2 = M.robustness_gate(mutated_memory, recorded)
    result["robustness_gate"] = gate2

    # 3) GUARDRAIL: oracle correctness across normal + sabotage runs.
    sabotage = guardrail_runs()
    oracle = M.oracle_error_rates(memory + mutated_memory + sabotage)
    result["oracle_error_rates"] = oracle

    result["verdict"] = M.verdict(gate1, gate2, oracle)
    all_runs = memory + cold + mutated_memory + recorded + sabotage
    M.write_report(all_runs)
    M.write_markdown(result, all_runs)
    return result


def main() -> None:
    result = run_experiment()
    print("=" * 72)
    print("Measurement 1 — existential gate (memory vs cold agent):")
    for k, v in result["existential_gate"].items():
        print(f"    {k}: {v}")
    if "robustness_gate" in result:
        print("Measurement 2 — robustness (memory vs recorded script, post-mutation):")
        for k, v in result["robustness_gate"].items():
            print(f"    {k}: {v}")
    if "oracle_error_rates" in result:
        print("Guardrail — oracle error rates:")
        for k, v in result["oracle_error_rates"].items():
            print(f"    {k}: {v}")
    print("-" * 72)
    print("VERDICT:", result["verdict"])
    print("=" * 72)
    print("NOTE: numbers above are from the deterministic `simapp` stand-in "
          "(assumptions, not\nmeasurements). Run the live Browser Use arm for an "
          "empirical gate. See README.md.")


if __name__ == "__main__":
    main()
