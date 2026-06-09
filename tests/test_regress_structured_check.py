"""The structured-CHECK matcher path + the delete-a-campaign reproduction and
the load-bearing no-false-pass guard (ADR-0031 steps 4, 7).

The live failure: `delete-a-campaign` has a believed success signal "a fresh
list load returns exactly one fewer campaign and no longer includes the archived
id". That is a count delta plus an after-action absence, a RELATION no string
invariant can carry, so neither Jaccard nor an ADR-0030 predicate could confirm
it, and the genuinely-passing goal read UNCERTAIN -> a false REGRESSED.

With a structured `check` the body evaluates the assertion over the agent's
self-reported data (before/after counts, identifier + membership). The
no-false-pass tests prove the structured path FAILS CLOSED and is STRICTER than
every string path: a no-op delta, a still-present element, or a missing payload
does NOT match.
"""
from __future__ import annotations

from datetime import datetime, timezone

from praxis.model import (
    ElementMembershipCheck,
    KnowledgeFile,
    ListCountDeltaCheck,
    Meta,
    Provenance,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
)
from praxis.runner import RegressionVerdict, verdict_from_observations
from praxis.runner.regression import _value_matches
from praxis.store import ObservedSignal


def _prov() -> Provenance:
    return Provenance(
        source_type=SourceType.HUMAN, source_id="pablo-seed",
        last_verified=datetime(2026, 6, 9, tzinfo=timezone.utc),
        observation_count=1,
    )


def _seed(type_: SignalType, value: str, check: object) -> Signal:
    return Signal(type=type_, value=value, check=check,  # type: ignore[arg-type]
                  provenance=_prov(), confidence=1.0, status=Status.BELIEVED)


def _obs(kind: str, type_: SignalType, value: str,
         observed: dict | None) -> ObservedSignal:
    return ObservedSignal(kind=kind, type=type_, value=value, observed=observed,
                          source_type=SourceType.AGENT, source_id="regress-agent")


_COUNT_SEED = _seed(
    SignalType.NETWORK,
    "a fresh list load returns one fewer campaign",
    ListCountDeltaCheck(expect_delta=-1),
)
_MEMBER_SEED = _seed(
    SignalType.NETWORK,
    "the archived campaign id is gone from the list",
    ElementMembershipCheck(identifier_slot="campaign_id", expect="absent"),
)


# --- the check branch matches a grounded observation -------------------------


def test_count_delta_matches_on_minus_one() -> None:
    obs = _obs("success", SignalType.NETWORK, "list went from 15 to 14",
               {"before_count": 15, "after_count": 14})
    assert _value_matches(obs, _COUNT_SEED) is True


def test_membership_absent_matches_when_archived_id_gone() -> None:
    obs = _obs("success", SignalType.NETWORK, "329419 not in the list anymore",
               {"identifier": "329419", "present": False})
    assert _value_matches(obs, _MEMBER_SEED) is True


def test_check_respects_exact_type_gate() -> None:
    # ADR-0028 exact-type equality still gates first: a behavioral observation
    # cannot match a network check even with a satisfying payload.
    obs = _obs("success", SignalType.BEHAVIORAL, "list went from 15 to 14",
               {"before_count": 15, "after_count": 14})
    assert _value_matches(obs, _COUNT_SEED) is False


# --- no false PASS: the structured check is STRICTER and fails closed --------


def test_count_delta_noop_does_not_match() -> None:
    # Planted regression: archive removed nothing.
    obs = _obs("success", SignalType.NETWORK, "list stayed at 15",
               {"before_count": 15, "after_count": 15})
    assert _value_matches(obs, _COUNT_SEED) is False


def test_membership_still_present_does_not_match() -> None:
    # Planted regression: the archived id is still there.
    obs = _obs("success", SignalType.NETWORK, "329419 still listed",
               {"identifier": "329419", "present": True})
    assert _value_matches(obs, _MEMBER_SEED) is False


def test_check_fails_closed_on_missing_payload() -> None:
    obs = _obs("success", SignalType.NETWORK, "I think it worked", None)
    assert _value_matches(obs, _COUNT_SEED) is False


# --- end to end: the verdict turns the false REGRESSED into a real PASS -------


def _kf(signals: list[Signal]) -> KnowledgeFile:
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    return KnowledgeFile(
        schema_version="0", goal_id="delete-a-campaign",
        goal="a user can archive a campaign", target=Target(app="digioh"),
        success_signals=signals, meta=Meta(created_at=now, updated_at=now),
    )


def test_verdict_passes_when_both_checks_hold() -> None:
    kf = _kf([_COUNT_SEED, _MEMBER_SEED])
    obs = [
        _obs("success", SignalType.NETWORK, "15 to 14",
             {"before_count": 15, "after_count": 14}),
        _obs("success", SignalType.NETWORK, "329419 gone",
             {"identifier": "329419", "present": False}),
    ]
    verdict, matched, _ = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.PASS
    assert len(matched) == 2


def test_verdict_uncertain_when_a_check_does_not_hold() -> None:
    # A planted regression (no-op archive) leaves the count check unmatched, so
    # the run is UNCERTAIN (mapped to REGRESSED by the aggregate), never PASS.
    kf = _kf([_COUNT_SEED, _MEMBER_SEED])
    obs = [
        _obs("success", SignalType.NETWORK, "stayed at 15",
             {"before_count": 15, "after_count": 15}),
        _obs("success", SignalType.NETWORK, "329419 gone",
             {"identifier": "329419", "present": False}),
    ]
    verdict, matched, _ = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.UNCERTAIN
    assert len(matched) == 1
