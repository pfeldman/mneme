"""Unit tests for structured signal checks (ADR-0031).

These cover the typed discriminated union (write-boundary validation, decision
6) and `evaluate_check` (decisions 3, 5). The load-bearing guard is that the
structured path FAILS CLOSED and is STRICTER than every string path: a no-op
delta, a still-present element, or a missing / malformed observation must NOT
evaluate as holding (no false PASS, AGENTS.md non-negotiable 5).
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from praxis.model.check import (
    Check,
    ElementMembershipCheck,
    ListCountDeltaCheck,
    evaluate_check,
)

_CHECK = TypeAdapter(Check)


# --- write-boundary validation (decision 6) ----------------------------------


def test_union_dispatches_on_kind() -> None:
    a = _CHECK.validate_python({"kind": "list_count_delta", "expect_delta": -1})
    assert isinstance(a, ListCountDeltaCheck)
    assert a.expect_delta == -1

    b = _CHECK.validate_python(
        {"kind": "element_membership", "identifier_slot": "campaign_id",
         "expect": "absent"}
    )
    assert isinstance(b, ElementMembershipCheck)
    assert b.expect == "absent"


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _CHECK.validate_python({"kind": "http_status", "code": 200})


def test_list_count_delta_requires_integer_delta() -> None:
    with pytest.raises(ValidationError):
        _CHECK.validate_python(
            {"kind": "list_count_delta", "expect_delta": "minus one"}
        )
    with pytest.raises(ValidationError):
        _CHECK.validate_python({"kind": "list_count_delta"})


def test_element_membership_rejects_empty_or_malformed_slot() -> None:
    with pytest.raises(ValidationError):
        _CHECK.validate_python(
            {"kind": "element_membership", "identifier_slot": "",
             "expect": "absent"}
        )
    with pytest.raises(ValidationError):
        _CHECK.validate_python(
            {"kind": "element_membership", "identifier_slot": "has space",
             "expect": "absent"}
        )


def test_element_membership_rejects_bad_expect() -> None:
    with pytest.raises(ValidationError):
        _CHECK.validate_python(
            {"kind": "element_membership", "identifier_slot": "id",
             "expect": "gone"}
        )


def test_extra_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _CHECK.validate_python(
            {"kind": "list_count_delta", "expect_delta": -1, "fuzz": 0.5}
        )


# --- evaluate: list_count_delta (decision 5) ---------------------------------


def test_count_delta_holds_on_exact_delta() -> None:
    chk = ListCountDeltaCheck(expect_delta=-1)
    assert evaluate_check(chk, {"before_count": 15, "after_count": 14}) is True


def test_count_delta_noop_does_not_hold() -> None:
    # The planted-regression case: archive removed nothing, after == before.
    chk = ListCountDeltaCheck(expect_delta=-1)
    assert evaluate_check(chk, {"before_count": 15, "after_count": 15}) is False


def test_count_delta_wrong_delta_does_not_hold() -> None:
    chk = ListCountDeltaCheck(expect_delta=-1)
    assert evaluate_check(chk, {"before_count": 15, "after_count": 13}) is False


def test_count_delta_fails_closed_on_missing_or_bad_payload() -> None:
    chk = ListCountDeltaCheck(expect_delta=-1)
    assert evaluate_check(chk, None) is False
    assert evaluate_check(chk, {}) is False
    assert evaluate_check(chk, {"before_count": 15}) is False
    assert evaluate_check(chk, {"before_count": "15", "after_count": "14"}) is False
    # bool is an int subclass; it must NOT be accepted as a count.
    assert evaluate_check(chk, {"before_count": True, "after_count": False}) is False


# --- evaluate: element_membership (decision 5) -------------------------------


def test_membership_absent_holds_when_absent() -> None:
    chk = ElementMembershipCheck(identifier_slot="campaign_id", expect="absent")
    assert evaluate_check(chk, {"identifier": "329419", "present": False}) is True


def test_membership_absent_does_not_hold_when_still_present() -> None:
    # The planted-regression case: the archived id is still in the list.
    chk = ElementMembershipCheck(identifier_slot="campaign_id", expect="absent")
    assert evaluate_check(chk, {"identifier": "329419", "present": True}) is False


def test_membership_present_holds_when_present() -> None:
    chk = ElementMembershipCheck(identifier_slot="campaign_id", expect="present")
    assert evaluate_check(chk, {"identifier": "329419", "present": True}) is True


def test_membership_fails_closed_on_missing_or_bad_payload() -> None:
    chk = ElementMembershipCheck(identifier_slot="campaign_id", expect="absent")
    assert evaluate_check(chk, None) is False
    assert evaluate_check(chk, {}) is False
    assert evaluate_check(chk, {"identifier": "", "present": False}) is False
    assert evaluate_check(chk, {"identifier": "   ", "present": False}) is False
    assert evaluate_check(chk, {"identifier": "329419"}) is False
    # present must be a real bool, not a truthy/falsy stand-in.
    assert evaluate_check(chk, {"identifier": "329419", "present": "no"}) is False
    assert evaluate_check(chk, {"identifier": "329419", "present": 0}) is False
