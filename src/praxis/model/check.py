"""Structured signal checks: typed assertions evaluated over observation data
(ADR-0031).

A signal's optional `check` is the THIRD, stricter tier above the ADR-0030
`value_predicate`. Where a `value_predicate` is a string invariant the matcher
string-matches (right for a stable phrase with per-run instance tokens), a
`check` is a TYPED ASSERTION the matcher EVALUATES PROGRAMMATICALLY over the
structured data the agent observed. It is the only way to express a fact that is
a RELATION (a count delta) or an AFTER-ACTION state (a membership change) that no
phrase can carry: "the list returns exactly one fewer campaign", "the archived id
is no longer present".

The vocabulary is deliberately tiny (ADR-0031 decision 2), mirroring the
conservative `trigger` discriminated union and the `trigger_validator`
banned-phrase discipline:

  - `list_count_delta`  -> the RELATIONAL primitive. The agent reports a BEFORE
    and an AFTER count; the body checks `after - before == expect_delta`.
  - `element_membership` -> the AFTER-ACTION primitive. The agent reports a
    per-run identifier and whether it is present after the action; the body
    checks the observed membership equals the expected state.

`evaluate_check` is the load-bearing FALSE-PASS guard (AGENTS.md non-negotiable
5, docs/06): it is STRICTER than every string path and FAILS CLOSED. A missing or
malformed observation (no payload, a missing or non-int count, an empty
identifier, a non-bool membership) is a NON-match, never a free pass. The agent
reports raw observed data; it never self-certifies that the check holds (ADR-0019,
ADR-0028).

Pure stdlib + pydantic, zero runtime/browser deps (ADR-0003, AGENTS.md
non-negotiable 4): the discriminated union validates at the write boundary (the
same posture as `model.predicate` and `trigger_validator`) and `evaluate_check`
runs in the matcher.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypeGuard, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A check's identifier slot is a per-run instance token name, reusing the same
# slot-name shape the ADR-0030 predicate parser enforces so the two stay one
# rule. `predicate` imports nothing from this module, so this import is one-way.
from .predicate import _SLOT_NAME_RE


class _CheckBase(BaseModel):
    # extra="forbid" mirrors `additionalProperties: false` in the JSON Schema and
    # makes an unknown field on a check a loud rejection at the write boundary.
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class ListCountDeltaCheck(_CheckBase):
    """A relational check: the observed list changed by an exact integer count
    (ADR-0031 decision 2).

    `expect_delta` is the required signed change (e.g. -1 for "exactly one
    fewer"). The agent observes a `before_count` and an `after_count`; the body
    checks `after_count - before_count == expect_delta`. There is no tolerance:
    a no-op (after == before) cannot satisfy a -1 delta, so a regression that
    fails to remove is a loud non-match (no false PASS).
    """

    kind: Literal["list_count_delta"] = "list_count_delta"
    expect_delta: int


class ElementMembershipCheck(_CheckBase):
    """An after-action check: a per-run identifier is present or absent after the
    action (ADR-0031 decision 2).

    `identifier_slot` names the per-run instance token the agent must track (the
    archived campaign id), the same variable-slot convention ADR-0030 uses; it
    is prompt-facing and documentary (the seed never stores a concrete id).
    `expect` is `present` or `absent`. The agent observes the concrete
    identifier and whether it is in the set after the action; the body checks the
    observed membership equals `expect`.
    """

    kind: Literal["element_membership"] = "element_membership"
    identifier_slot: str = Field(min_length=1)
    expect: Literal["present", "absent"]

    @field_validator("identifier_slot")
    @classmethod
    def _slot_is_valid(cls, v: str) -> str:
        """Reject a malformed identifier slot at the write boundary (ADR-0031
        decision 6), reusing the ADR-0030 slot-name shape so the two never
        drift."""
        if not _SLOT_NAME_RE.match(v):
            raise ValueError(
                f"element_membership identifier_slot {v!r} is malformed "
                f"(expected a simple identifier: letters / digits / underscore)"
            )
        return v


# The discriminated union, mirroring `Trigger` in knowledge.py: an unknown
# `kind` is rejected by pydantic at the write boundary, so a malformed check
# never reaches the matcher (ADR-0031 decision 6).
Check = Annotated[
    Union[ListCountDeltaCheck, ElementMembershipCheck],
    Field(discriminator="kind"),
]


def evaluate_check(check: Check, observed: dict[str, Any] | None) -> bool:
    """True iff the OBSERVED structured data satisfies this check (ADR-0031
    decision 5). FAILS CLOSED.

    The agent self-reports the structured payload (ADR-0031 decision 3); the body
    evaluates the assertion here. A None / missing / malformed payload is a
    NON-match, never a free pass: an un-reportable or unsatisfied check fails
    CLOSED (loud), it does not fall through to a looser path. The agent never
    self-certifies that the check holds; it only supplies the raw data this
    function reduces to a boolean.
    """
    if observed is None:
        return False

    if isinstance(check, ListCountDeltaCheck):
        before = observed.get("before_count")
        after = observed.get("after_count")
        # bool is an int subclass; reject it explicitly so a True/False count is
        # not silently treated as 1/0.
        if not _is_int(before) or not _is_int(after):
            return False
        return (after - before) == check.expect_delta

    if isinstance(check, ElementMembershipCheck):
        identifier = observed.get("identifier")
        present = observed.get("present")
        # A missing / empty identifier or a non-bool membership is a non-match:
        # the agent must have actually tracked the per-run token and reported a
        # real boolean, never an absent or fuzzy value.
        if not isinstance(identifier, str) or not identifier.strip():
            return False
        if not isinstance(present, bool):
            return False
        return present == (check.expect == "present")

    # Unreachable: the union is exhaustive and validated at the boundary. Fail
    # closed anyway so a future kind added without a branch is a non-match, never
    # a silent pass.
    return False


def _is_int(v: Any) -> TypeGuard[int]:
    """An int that is NOT a bool (bool is an int subclass in Python).

    A `TypeGuard` so the matcher narrows the operands to `int` before the
    subtraction; the bool exclusion stays a runtime guard so a True/False count
    is a non-match, not a silent 1/0.
    """
    return isinstance(v, int) and not isinstance(v, bool)
