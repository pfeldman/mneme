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


# --- Phase-1 schema activation: risks + uncertainties + triggers -------------


def test_risk_required_fields_match() -> None:
    from praxis.model import Risk

    schema_required = set(SCHEMA["$defs"]["risk"]["required"])
    model_required = {
        name for name, f in Risk.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_uncertainty_required_fields_match() -> None:
    from praxis.model import Uncertainty

    schema_required = set(SCHEMA["$defs"]["uncertainty"]["required"])
    model_required = {
        name for name, f in Uncertainty.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_trigger_discriminator_dispatches_http_vs_sequence() -> None:
    from praxis.model import HttpTrigger, Risk, SequenceTrigger

    base_provenance = {
        "source_type": "human", "source_id": "AC-RISK-1",
        "last_verified": "2026-06-07T00:00:00Z", "observation_count": 1,
    }
    http = Risk.model_validate({
        "id": "r1", "description": "x",
        "trigger": {"kind": "http", "method": "POST", "path": "/x", "expect": "y"},
        "provenance": base_provenance, "confidence": 1.0, "status": "believed",
    })
    seq = Risk.model_validate({
        "id": "r2", "description": "x",
        "trigger": {"kind": "sequence", "n": 2, "action": "z", "expect": "y"},
        "provenance": base_provenance, "confidence": 1.0, "status": "believed",
    })
    assert isinstance(http.trigger, HttpTrigger)
    assert isinstance(seq.trigger, SequenceTrigger)


def test_trigger_rejects_free_text_and_invalid_method() -> None:
    import pytest
    from pydantic import ValidationError

    from praxis.model import Risk

    base_provenance = {
        "source_type": "human", "source_id": "x",
        "last_verified": "2026-06-07T00:00:00Z", "observation_count": 1,
    }
    # Plain-string trigger is rejected (no `kind` discriminator).
    with pytest.raises(ValidationError):
        Risk.model_validate({
            "id": "r", "description": "x",
            "trigger": "under high load",
            "provenance": base_provenance, "confidence": 1.0, "status": "believed",
        })
    # HTTP-shaped trigger with an unsupported method is rejected.
    with pytest.raises(ValidationError):
        Risk.model_validate({
            "id": "r", "description": "x",
            "trigger": {"kind": "http", "method": "FETCH", "path": "/x", "expect": "y"},
            "provenance": base_provenance, "confidence": 1.0, "status": "believed",
        })


def test_phase_0_files_still_validate_without_risks_or_uncertainties() -> None:
    """Schema additions are additive: a Phase-0 file lacking risks/uncertainties
    must still validate against the active Phase-1 schema (ADR-0009)."""
    from praxis.model import KnowledgeFile

    minimal = {
        "schema_version": "0",
        "goal_id": "g",
        "goal": "a user can do X",
        "target": {"app": "acme"},
        "success_signals": [{
            "type": "behavioral", "value": "v",
            "provenance": {"source_type": "agent", "source_id": "a1",
                            "last_verified": "2026-06-07T00:00:00Z",
                            "observation_count": 1},
            "confidence": 0.5, "status": "contested",
        }],
        "meta": {"created_at": "2026-06-07T00:00:00Z",
                 "updated_at": "2026-06-07T00:00:00Z"},
    }
    kf = KnowledgeFile.model_validate(minimal)
    assert kf.risks is None
    assert kf.uncertainties is None
