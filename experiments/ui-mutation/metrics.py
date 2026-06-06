"""Metrics for the UI-mutation experiment. See README.md.

Three numbers decide the project. Measurement ORDER matters: the cold-agent cost
comparison is the existential gate and is checked FIRST (docs/06).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json
from statistics import mean


@dataclass
class RunResult:
    arm: str                 # "memory" | "cold_agent" | "recorded_script"
    flow: str                # "login" | "search" | "checkout"
    mutated: bool            # was a UI mutation applied for this run?
    succeeded: bool          # did the run actually reach the goal (ground truth)?
    tokens: int = 0
    wall_seconds: float = 0.0
    actions: int = 0         # browser actions taken — the cost proxy when an LLM
                             # subscription hides per-task token counts (local run)
    oracle_said_success: bool | None = None    # what the oracle reported
    ground_truth_success: bool | None = None   # human/spec ground truth


def _ok(rs: list[RunResult]) -> float:
    return (sum(r.succeeded for r in rs) / len(rs)) if rs else 0.0


def _cost(rs: list[RunResult]) -> tuple[float, str]:
    """Average cost + the unit used. Prefers tokens; falls back to browser actions
    when tokens are unavailable (a flat-rate subscription hides per-task tokens)."""
    if not rs:
        return 0.0, "tokens"
    if any(r.tokens for r in rs):
        return mean([r.tokens for r in rs]), "tokens"
    return mean([r.actions for r in rs]), "actions"


# --- Measurement 1: existential gate (memory vs cold agent) ---
def existential_gate(memory: list[RunResult], cold: list[RunResult]) -> dict:
    """memory must be cheaper AND at least as reliable as a cold agent."""
    m_cost, unit = _cost(memory)
    c_cost, _ = _cost(cold)
    passed = (m_cost < c_cost) and (_ok(memory) >= _ok(cold))
    return {
        "cost_unit": unit,
        "memory_success": _ok(memory), "cold_success": _ok(cold),
        "memory_avg_cost": m_cost, "cold_avg_cost": c_cost,
        "cost_ratio_memory_over_cold": (m_cost / c_cost) if c_cost else None,
        "PASSED": passed,
    }


# --- Measurement 2: robustness (memory vs recorded script, post-mutation) ---
def recovery_rate(arm: list[RunResult]) -> float:
    mutated = [r for r in arm if r.mutated]
    return _ok(mutated)


def robustness_gate(memory: list[RunResult], recorded: list[RunResult]) -> dict:
    return {
        "memory_recovery": recovery_rate(memory),
        "recorded_recovery": recovery_rate(recorded),
        "PASSED": recovery_rate(memory) > recovery_rate(recorded),
    }


# --- Cross-cutting guardrail: oracle correctness ---
def oracle_error_rates(results: list[RunResult]) -> dict:
    scored = [r for r in results
              if r.oracle_said_success is not None and r.ground_truth_success is not None]
    if not scored:
        return {"false_pass": None, "false_fail": None, "n": 0}
    fp = sum(r.oracle_said_success and not r.ground_truth_success for r in scored)
    ff = sum((not r.oracle_said_success) and r.ground_truth_success for r in scored)
    return {"false_pass": fp / len(scored), "false_fail": ff / len(scored), "n": len(scored)}


BRITTLE_FALSE_PASS_BASELINE = 0.05  # oracle false-pass must beat this to be worth it


def verdict(gate1: dict, gate2: dict, oracle: dict) -> str:
    """Apply the kill/continue gate IN ORDER (docs/04). Continue only if all three
    clear: cost edge over cold, robustness edge over a recorded script, and an
    oracle false-pass rate below brittle-test levels."""
    if not gate1["PASSED"]:
        return "STOP — fails M1: no cost/reliability edge over a cold agent."
    if not gate2["PASSED"]:
        return "STOP — passes M1 but fails M2: no robustness edge over a recorded script."
    fp = oracle.get("false_pass")
    if fp is None:
        return "INCONCLUSIVE — M1 and M2 pass but the oracle was never scored (no ground truth)."
    if fp > BRITTLE_FALSE_PASS_BASELINE:
        return (f"STOP — M1/M2 pass but oracle false-pass {fp:.2%} exceeds the brittle-test "
                f"baseline ({BRITTLE_FALSE_PASS_BASELINE:.0%}).")
    return "CONTINUE — clears all three gates (cost, robustness, oracle false-pass)."


def write_report(results: list[RunResult], path: str = "experiments/ui-mutation/results.json") -> None:
    with open(path, "w") as fh:
        json.dump([asdict(r) for r in results], fh, indent=2)


def write_markdown(result: dict, results: list[RunResult],
                   path: str = "experiments/ui-mutation/results.md") -> None:
    """Human-readable summary of one experiment run."""
    lines = ["# UI-mutation experiment — results", ""]
    lines.append("> Numbers below come from the deterministic `simapp` stand-in "
                 "(token costs are stated assumptions, not measurements). They "
                 "validate the harness/metrics/oracle wiring and the kill/continue "
                 "logic, not the thesis. Run the live Browser Use arm for empirical "
                 "numbers.")
    lines.append("")
    lines.append("## Measurement 1 — existential gate (memory vs cold agent)")
    for k, v in result.get("existential_gate", {}).items():
        lines.append(f"- **{k}**: {v}")
    if "robustness_gate" in result:
        lines.append("")
        lines.append("## Measurement 2 — robustness (memory vs recorded script)")
        for k, v in result["robustness_gate"].items():
            lines.append(f"- **{k}**: {v}")
    if "oracle_error_rates" in result:
        lines.append("")
        lines.append("## Guardrail — oracle error rates")
        for k, v in result["oracle_error_rates"].items():
            lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append(f"## Verdict\n\n**{result.get('verdict', 'n/a')}**")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
