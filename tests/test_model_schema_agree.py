"""The JSON Schema is the single source of truth for shape (ADR-0002); the pydantic
model mirrors it. This test asserts they agree on the load-bearing parts, so the two
cannot silently drift."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schema" / "knowledge.schema.json").read_text())


def test_top_level_required_fields_match() -> None:
    from praxis.model import KnowledgeFile

    schema_required = set(SCHEMA["required"])
    model_required = {
        name for name, f in KnowledgeFile.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_signal_required_fields_match() -> None:
    from praxis.model import Signal

    schema_required = set(SCHEMA["$defs"]["signal"]["required"])
    model_required = {
        name for name, f in Signal.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_provenance_required_fields_match() -> None:
    from praxis.model import Provenance

    schema_required = set(SCHEMA["$defs"]["provenance"]["required"])
    model_required = {
        name for name, f in Provenance.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_enums_match() -> None:
    from praxis.model import SignalType, SourceType, Status

    assert [t.value for t in SignalType] == SCHEMA["$defs"]["signal"]["properties"]["type"]["enum"]
    assert ([s.value for s in SourceType]
            == SCHEMA["$defs"]["provenance"]["properties"]["source_type"]["enum"])
    assert [s.value for s in Status] == SCHEMA["$defs"]["status"]["enum"]


def test_signal_type_order_is_most_to_least_durable() -> None:
    # The oracle relies on this ordering being meaningful (different types are
    # independent evidence); freeze it so a reorder is a conscious change.
    from praxis.model import SignalType

    assert [t.value for t in SignalType] == [
        "behavioral", "network", "accessibility", "text", "url", "visual",
    ]


def test_model_rejects_signal_without_provenance() -> None:
    import pytest
    from pydantic import ValidationError

    from praxis.model import Signal

    with pytest.raises(ValidationError):
        Signal.model_validate({"type": "behavioral", "value": "x",
                               "confidence": 0.9, "status": "believed"})
