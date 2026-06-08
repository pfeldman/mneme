"""Unit tests for the structured signal predicate (ADR-0030).

These cover the parser/validator (decisions 5, 6) and the evaluator (decisions
2, 3): the load-bearing guard is that the structured path is STRICTER than
Jaccard, never looser. A wrong invariant (wrong status code, wrong route) must
NOT evaluate as holding; only a declared slot is tolerant, and only on
presence/shape.
"""
from __future__ import annotations

import pytest

from praxis.model.predicate import PredicateError, parse


# --- parse + validate: accept the well-formed shapes -------------------------


def test_parse_extracts_invariant_and_ordered_slots() -> None:
    p = parse(
        "GET account.digioh.com/ returns 2xx and the campaign list contains a "
        "row whose id equals {campaign_id}"
    )
    assert [s.name for s in p.slots] == ["campaign_id"]
    assert p.slots[0].shape is None


def test_parse_typed_slots() -> None:
    p = parse("the route matches /Box/Editor/{seg:numeric}")
    assert p.slots[0].name == "seg"
    assert p.slots[0].shape == "numeric"

    p2 = parse("the record id is {rec:uuid}")
    assert p2.slots[0].shape == "uuid"


def test_parse_multiple_slots_keeps_order() -> None:
    p = parse("user {user} created campaign {campaign_id:numeric}")
    assert [(s.name, s.shape) for s in p.slots] == [
        ("user", None), ("campaign_id", "numeric"),
    ]


# --- parse + validate: reject the malformed / invariant-less shapes ----------


def test_reject_predicate_with_no_invariant_just_one_slot() -> None:
    # A predicate that is nothing but a slot would match everything (decision 3).
    with pytest.raises(PredicateError):
        parse("{anything}")


def test_reject_empty_predicate() -> None:
    with pytest.raises(PredicateError):
        parse("")
    with pytest.raises(PredicateError):
        parse("   ")


def test_reject_stopword_only_invariant() -> None:
    # "the {x}" carries no durable invariant token (decision 6).
    with pytest.raises(PredicateError):
        parse("the {x}")
    with pytest.raises(PredicateError):
        parse("a {x} of {y}")


def test_reject_malformed_slot_unbalanced_braces() -> None:
    with pytest.raises(PredicateError):
        parse("returns 2xx and id equals {campaign_id")
    with pytest.raises(PredicateError):
        parse("returns 2xx and id equals campaign_id}")


def test_reject_empty_slot_name() -> None:
    with pytest.raises(PredicateError):
        parse("route is /Box/Editor/{}")


def test_reject_unknown_shape() -> None:
    with pytest.raises(PredicateError):
        parse("route is /Box/Editor/{seg:semver}")
    with pytest.raises(PredicateError):
        parse("route is /Box/Editor/{seg:regex}")


def test_reject_duplicate_slot_name() -> None:
    with pytest.raises(PredicateError):
        parse("id {x} equals id {x}")


# --- evaluate: the invariant is matched EXACTLY (stricter than Jaccard) ------


def test_exact_invariant_holds() -> None:
    p = parse("a create endpoint returns 2xx for campaign {campaign_id}")
    assert p.evaluate("a create endpoint returns 2xx for campaign 329419")


def test_invariant_is_contained_not_whole_string_equal() -> None:
    """Containment, not whole-string equality: the invariant (with its slot) may
    be wrapped in an agent's narration and still hold, so an LLM's run-to-run
    phrasing variance does not drop a genuine pass. A wrong invariant or a
    shape-violating slot still does NOT hold (no false pass)."""
    p = parse("the route matches /Box/Editor/{campaign_id:numeric}")
    # Surrounding narration before and after the invariant: still holds.
    assert p.evaluate("After saving, the route matches /Box/Editor/329419 successfully")
    assert p.evaluate("the route matches /Box/Editor/329419")
    # No false pass: a wrong route, or a non-numeric slot, still does not hold.
    assert not p.evaluate("After saving, the route matches /Account/Login/Index")
    assert not p.evaluate("the route matches /Box/Editor/welcome and it worked")


def test_case_and_whitespace_normalized_but_not_punctuation() -> None:
    p = parse("the route matches /Box/Editor/{seg:numeric}")
    # case-folded + whitespace-collapsed still holds
    assert p.evaluate("THE   route   matches /Box/Editor/329419")
    # the invariant text must still be present: a different route does NOT hold
    assert not p.evaluate("the route matches /Box/Viewer/329419")


def test_wrong_status_code_does_not_hold() -> None:
    # The whole point: `returns 500` cannot satisfy a `returns 2xx` invariant,
    # where 0.5 Jaccard on the shared words could have admitted it.
    p = parse("a create endpoint returns 2xx for campaign {campaign_id}")
    assert not p.evaluate("a create endpoint returns 500 for campaign 329419")


def test_wrong_route_does_not_hold() -> None:
    p = parse("the route matches /Box/Editor/{seg:numeric}")
    assert not p.evaluate("the route matches /Account/Login/329419")


# --- evaluate: a declared slot is tolerant ONLY on presence (+ optional shape) -


def test_slot_filled_holds_regardless_of_instance_token() -> None:
    p = parse("banner text contains Created Campaign {campaign_id}")
    # two different per-run instance tokens both hold (slot is not compared
    # literally between seed and run)
    assert p.evaluate("banner text contains Created Campaign 329419")
    assert p.evaluate("banner text contains Created Campaign 42")


def test_empty_slot_does_not_hold() -> None:
    # An empty / missing slot filler is a NON-match, never a free pass
    # (decision 3): the invariant text is present but the slot is unfilled.
    p = parse("banner text contains Created Campaign {campaign_id}")
    assert not p.evaluate("banner text contains Created Campaign ")
    assert not p.evaluate("banner text contains Created Campaign")


def test_numeric_shape_pass_and_fail() -> None:
    p = parse("the route matches /Box/Editor/{seg:numeric}")
    assert p.evaluate("the route matches /Box/Editor/329419")
    # a non-numeric route segment is itself a regression (decision 5): does NOT
    # hold even though the slot is filled.
    assert not p.evaluate("the route matches /Box/Editor/login")


def test_uuid_shape_pass_and_fail() -> None:
    p = parse("the record id is {rec:uuid}")
    assert p.evaluate("the record id is 550e8400-e29b-41d4-a716-446655440000")
    assert not p.evaluate("the record id is 329419")


def test_slot_does_not_swallow_following_invariant() -> None:
    # `\\S+` binds one token, so the trailing invariant word must still match.
    p = parse("campaign {campaign_id} was created")
    assert p.evaluate("campaign 329419 was created")
    assert not p.evaluate("campaign 329419 was deleted")
