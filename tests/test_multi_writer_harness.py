"""Wires the adversarial multi-writer harness into pytest so the CI gate
ADR-0012 section 4 names is impossible to skip silently.

The harness is the source of truth for the scenarios; this test just calls
`run_all()` and asserts every scenario passed. New scenarios go in the
harness, not here.
"""
from __future__ import annotations

from experiments.multi_writer.harness import run_all


def test_multi_writer_harness_all_scenarios_pass() -> None:
    results = run_all()
    failures = [r for r in results if not r.passed]
    assert not failures, "harness scenarios failed:\n" + "\n".join(
        r.as_line() for r in failures
    )
    # Sanity: confirm the ADR-0012 minimum scenarios all ran.
    expected_names = {
        "concurrent_same_source",
        "concurrent_diverse_source",
        "racing_contradiction",
        "racing_oscillation",
        "partial_write_failure",
    }
    got_names = {r.name for r in results}
    missing = expected_names - got_names
    assert not missing, f"harness is missing required scenarios: {missing}"
