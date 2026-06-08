"""Runner package: regression (R-mode) and exploration (E-mode).

The runner is the contract layer between believed knowledge and an executing
agent. It renders prompts the agent reads, then folds the observations the
agent emits into a verdict (R-mode) or candidate-knowledge events (E-mode).
The runner does NOT drive a browser; that is the adapter's job. This keeps
the core runtime-agnostic (ADR-0003, AGENTS.md non-negotiable 4): the runner
imports only model/store/merge/oracle.

See ADR-0009 for the mode definitions and `docs/phase-1-plan.md` for the
end-to-end shape.
"""
from __future__ import annotations

from .exploration import (
    ExplorationResult,
    ExplorationRunner,
    compute_off_path_fraction,
)
from .regression import (
    RegressionRunner,
    RegressionVerdict,
    RunResult,
    verdict_from_observations,
)
from .report import write_junit_xml, write_markdown_report

__all__ = [
    "ExplorationResult",
    "ExplorationRunner",
    "RegressionRunner",
    "RegressionVerdict",
    "RunResult",
    "compute_off_path_fraction",
    "verdict_from_observations",
    "write_junit_xml",
    "write_markdown_report",
]
