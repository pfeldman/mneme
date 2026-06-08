"""Projection / truth engine.

Folds the append-only event log into the *believed* `KnowledgeFile`: aggregates
repeated observations into confidence, classifies each assertion via the oracle
(believed / contested / stale / quarantined), applies recency decay, and PRESERVES
contradictions instead of silently picking a winner. This is where multi-agent
conflict resolution lives (docs/05). It NEVER does last-write-wins.

Phase 2 (ADR-0013) adds recency decay: a projection-time derivation plus a
sibling event kind (`DecayEvent`) that the projection driver writes when
re-evaluation over the surviving non-staled set demotes a signal to `stale`.

Phase 2 (ADR-0014) adds a sibling projection over `CandidateEvent`s: agent-
proposed risks and uncertainties default to `contested` and promote via the
SAME diversity-or-seed gate the signal projection uses.

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
    project_candidates   -- CandidateEvents (+ seed) -> list[ProjectedCandidate]
    contested_candidates -- filter to status=contested (for `praxis review`)
    ProjectedCandidate   -- projected candidate shape consumed by `praxis review`
"""
from __future__ import annotations

from .candidates import (
    ProjectedCandidate,
    contested_candidates,
    project_candidates,
)
from .decay import DecayConfig, evaluate_decay, select_current_version
from .projection import project, project_with_decay, project_with_seed

__all__ = [
    "DecayConfig",
    "ProjectedCandidate",
    "contested_candidates",
    "evaluate_decay",
    "project",
    "project_candidates",
    "project_with_decay",
    "project_with_seed",
    "select_current_version",
]
