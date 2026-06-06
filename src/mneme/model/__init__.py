"""Typed knowledge model.

Pydantic v2 models mirroring the **Phase-0** schema (`schema/knowledge.schema.json`).
Every assertion (a `Signal`) MUST carry `provenance` + `confidence` + `status`
(ADR-0004); the model structurally enforces this — a signal without provenance
fails validation. This module has ZERO runtime/browser dependencies (ADR-0003).

NOTE: This is the minimal Phase-0 model. The richer Phase-1 schema
(states / paths / risks / uncertainties) is intentionally NOT implemented here.

Public API:
    Provenance, Signal, Target, Meta, KnowledgeFile  -- the model
    load / dump / loads / dumps                        -- YAML round-trip
    validate_against_json_schema                       -- cross-check vs the JSON Schema
    SignalType, SourceType, Status                     -- enums (str)
"""
from __future__ import annotations

from .knowledge import (
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
    dump,
    dumps,
    load,
    loads,
    to_jsonable,
    validate_against_json_schema,
)

__all__ = [
    "KnowledgeFile",
    "Meta",
    "Provenance",
    "Signal",
    "SignalType",
    "SourceType",
    "Status",
    "Target",
    "dump",
    "dumps",
    "load",
    "loads",
    "to_jsonable",
    "validate_against_json_schema",
]
