"""Typed knowledge model.

Pydantic v2 models mirroring the active Phase-1 schema
(`schema/knowledge.schema.json`). Every assertion (a `Signal`, a `Risk`) MUST
carry `provenance` + `confidence` + `status` (ADR-0004); the model
structurally enforces this. Uncertainties are questions, not assertions, so
they carry author + timestamp instead. This module has ZERO runtime/browser
dependencies (ADR-0003).

Phase 1 activates `risks` (with discriminated-union triggers: HTTP or
sequence) and `uncertainties` as top-level arrays. `states` and `paths` stay
deferred (ADR-0009).

Public API:
    Provenance, Signal, Target, Meta                     - the signal model
    HttpTrigger, SequenceTrigger, Trigger, Risk          - Phase-1 risks
    Uncertainty                                          - Phase-1 uncertainties
    KnowledgeFile                                        - one goal entry
    load / dump / loads / dumps                          - YAML round-trip
    validate_against_json_schema                         - cross-check vs the JSON Schema
    SignalType, SourceType, Status                       - enums (str)
"""
from __future__ import annotations

from .knowledge import (
    HttpTrigger,
    KnowledgeFile,
    Meta,
    Provenance,
    Risk,
    SequenceTrigger,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
    Trigger,
    Uncertainty,
    dump,
    dumps,
    load,
    loads,
    to_jsonable,
    validate_against_json_schema,
)

__all__ = [
    "HttpTrigger",
    "KnowledgeFile",
    "Meta",
    "Provenance",
    "Risk",
    "SequenceTrigger",
    "Signal",
    "SignalType",
    "SourceType",
    "Status",
    "Target",
    "Trigger",
    "Uncertainty",
    "dump",
    "dumps",
    "load",
    "loads",
    "to_jsonable",
    "validate_against_json_schema",
]
