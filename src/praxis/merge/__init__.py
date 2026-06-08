"""Projection / truth engine.

Folds the append-only event log into the *believed* `KnowledgeFile`: aggregates
repeated observations into confidence, classifies each assertion via the oracle
(believed / contested / stale / quarantined), applies recency decay, and PRESERVES
contradictions instead of silently picking a winner. This is where multi-agent
conflict resolution lives (docs/05). It NEVER does last-write-wins.

Public API:
    project              -- events (+ explicit goal frame) -> believed KnowledgeFile
    project_with_seed    -- fold events onto a seeded knowledge file (cold-start)
    project_with_decay   -- Phase 2 projection that re-evaluates the diversity
                            gate over the surviving non-staled set and returns
                            (KnowledgeFile, new DecayEvents) per ADR-0013
    DecayConfig          -- pre-registered decay thresholds (N minor versions,
                            T wall-clock days)
    evaluate_decay       -- pure decay derivation entry-point (testable without
                            a store or browser)
    select_current_version -- multi-writer / decay collision resolver
                            (ADR-0013 section 5)
"""
from __future__ import annotations

from .decay import DecayConfig, evaluate_decay, select_current_version
from .projection import project, project_with_decay, project_with_seed

__all__ = [
    "DecayConfig",
    "evaluate_decay",
    "project",
    "project_with_decay",
    "project_with_seed",
    "select_current_version",
]
