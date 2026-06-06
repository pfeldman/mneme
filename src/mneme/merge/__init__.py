"""Projection / truth engine.

Folds the append-only event log into the *believed* `KnowledgeFile`: aggregates
repeated observations into confidence, classifies each assertion via the oracle
(believed / contested / stale / quarantined), applies recency decay, and PRESERVES
contradictions instead of silently picking a winner. This is where multi-agent
conflict resolution lives (docs/05). It NEVER does last-write-wins.

Public API:
    project              -- events (+ explicit goal frame) → believed KnowledgeFile
    project_with_seed    -- fold events onto a seeded knowledge file (cold-start)
"""
from __future__ import annotations

from .projection import project, project_with_seed

__all__ = ["project", "project_with_seed"]
