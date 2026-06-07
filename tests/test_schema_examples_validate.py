"""Smoke test: every example in schema/examples validates against the Phase-0
schema AND every assertion-like node carries provenance + confidence (ADR-0004)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "knowledge.schema.json"
EXAMPLES = sorted((ROOT / "schema" / "examples").glob("*.knowledge.yaml"))


def _example_ids() -> list[str]:
    return [p.name for p in EXAMPLES]


@pytest.mark.parametrize("example", EXAMPLES, ids=_example_ids())
def test_example_validates_against_json_schema(example: Path) -> None:
    import jsonschema
    import yaml

    schema = json.loads(SCHEMA.read_text())
    data = yaml.safe_load(example.read_text())
    jsonschema.validate(instance=data, schema=schema)


@pytest.mark.parametrize("example", EXAMPLES, ids=_example_ids())
def test_example_loads_via_model_and_round_trips(example: Path) -> None:
    from praxis.model import load, loads, dumps

    kf = load(example)
    assert loads(dumps(kf)) == kf  # YAML round-trip is lossless


@pytest.mark.parametrize("example", EXAMPLES, ids=_example_ids())
def test_every_signal_has_provenance_and_confidence(example: Path) -> None:
    from praxis.model import load

    kf = load(example)
    signals = list(kf.success_signals) + list(kf.failure_signals or [])
    assert signals, "an example must have at least one signal"
    for sig in signals:
        # Mandatory by ADR-0004; the model would reject otherwise, but assert the
        # intent explicitly so a future schema relaxation is caught here.
        assert sig.provenance is not None
        assert sig.provenance.source_id
        assert sig.provenance.observation_count >= 1
        assert 0.0 <= sig.confidence <= 1.0
        assert sig.status is not None
