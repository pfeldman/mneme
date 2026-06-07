"""Oracle validator -- the existential guardrail.

The hardest and most important module. A confidently-wrong success signal makes
shared memory worse than no memory (tests pass while the app is broken), so every
rule here favors making a wrong assertion LOUD and traceable over silent.

Trust model (ADR-0005):
  - A goal's success oracle is trustworthy (`oracle_believed`) when EITHER:
      (a) a success signal is SEEDED -- source_type in {human, spec}
          (trusted from cold-start), OR
      (b) >=2 success signals of DIFFERENT `type` agree (e.g. behavioral + network).
  - Agent count is NOT independence: two runs of the same model fail the same way.
    Repeated SAME-type observations only raise that one signal's confidence.
  - Signals whose presence flips across runs are `quarantined`.
  - The first oracle for a goal MUST be seeded, never self-certified.

Public API:
    SignalSummary           -- aggregated, time-ordered view of one (kind,type,value)
    TrustConfig             -- decay / staleness knobs
    is_flip_flop            -- oscillation detector → quarantine
    has_contradiction       -- positive vs negative disagreement → contested
    agreeing_types          -- distinct, consistently-present success types
    oracle_believed         -- the diversity-or-seed gate for a goal
    classify                -- per-signal Status
    confidence_of           -- per-signal confidence (count × recency; seed floor)
"""
from __future__ import annotations

from .trust import (
    SignalSummary,
    TrustConfig,
    agreeing_types,
    classify,
    confidence_of,
    has_contradiction,
    independent_diverse,
    is_flip_flop,
    is_stale,
    oracle_believed,
)

__all__ = [
    "SignalSummary",
    "TrustConfig",
    "agreeing_types",
    "classify",
    "confidence_of",
    "has_contradiction",
    "independent_diverse",
    "is_flip_flop",
    "is_stale",
    "oracle_believed",
]
