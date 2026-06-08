"""Banned-phrase validator tests for `Risk.trigger`.

The pydantic discriminated union (HttpTrigger / SequenceTrigger) gates
shape; `validate_trigger` is the textual floor that ADR-0009 sec 4
promised. The free-text vector that slipped past schema validation was
the `expect` field; these tests pin the banned-phrase rejection plus
the structural sanity checks.
"""
from __future__ import annotations

import pytest

from praxis.model import HttpTrigger, Risk, SequenceTrigger
from praxis.model.trigger_validator import (
    validate_risk,
    validate_trigger,
)


def _http(expect: str = "returns 200", path: str = "/x") -> HttpTrigger:
    return HttpTrigger(method="GET", path=path, expect=expect)


def _seq(expect: str = "same id both times", n: int = 2) -> SequenceTrigger:
    return SequenceTrigger(n=n, action="submit", expect=expect)


def test_accepts_concrete_http_predicate() -> None:
    out = validate_trigger(_http("returns 200 with applied=true"))
    assert out.outcome == "accepted"


def test_accepts_concrete_sequence_predicate() -> None:
    out = validate_trigger(_seq("both responses return the same order_id"))
    assert out.outcome == "accepted"


@pytest.mark.parametrize("banned", [
    "fails under high load",
    "the response is sometimes 500",
    "occasionally drops the cookie",
    "intermittently returns empty",
    "race condition between checkout and order",
    "the endpoint is flaky here",
    "sporadically returns wrong data",
    "may sometimes drop the filter",
])
def test_rejects_banned_phrases_in_expect(banned: str) -> None:
    out = validate_trigger(_http(expect=banned))
    assert out.outcome == "rejected"
    assert out.reason and "banned phrase" in out.reason


def test_rejects_http_path_without_leading_slash() -> None:
    # Bypass pydantic's path validation by passing a wholly-formed trigger
    # whose path looks shape-legal but starts wrong.
    bad = HttpTrigger.model_construct(
        kind="http", method="GET", path="x", body_or_params=None,
        expect="returns 200",
    )
    out = validate_trigger(bad)
    assert out.outcome == "rejected"


def test_validate_risk_dispatches_to_trigger() -> None:
    from datetime import datetime, timezone

    from praxis.model import Provenance, Status

    p = Provenance(source_type="human", source_id="x",  # type: ignore[arg-type]
                   last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
                   observation_count=1)
    risk = Risk(
        id="r", description="x",
        trigger=_http(expect="sometimes fails"),
        provenance=p, confidence=1.0, status=Status.CONTESTED,
    )
    out = validate_risk(risk)
    assert out.outcome == "rejected"
