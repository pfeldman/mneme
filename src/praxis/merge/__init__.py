"""Projection / truth engine.

Folds the append-only event log into the *believed* `KnowledgeFile`: aggregates
repeated observations into confidence, classifies each assertion via the oracle
(believed / contested / stale / quarantined), applies recency decay, and PRESERVES
contradictions instead of silently picking a winner. This is where multi-agent
conflict resolution lives (docs/05). It NEVER does last-write-wins.

Phase 2 (ADR-0014) adds a sibling projection over `CandidateEvent`s: agent-
proposed risks and uncertainties default to `contested` and promote via the
SAME diversity-or-seed gate the signal projection uses.

Public API:
    project              -- events (+ explicit goal frame) -> believed KnowledgeFile
    project_with_seed    -- fold events onto a seeded knowledge file (cold-start)
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
from .projection import project, project_with_seed

__all__ = [
    "ProjectedCandidate",
    "contested_candidates",
    "project",
    "project_candidates",
    "project_with_seed",
]
