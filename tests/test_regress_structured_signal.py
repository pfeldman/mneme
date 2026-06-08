"""The structured-signal matcher path + the create-welcome-popup reproduction
and the load-bearing no-false-pass guard (ADR-0030 steps 5, 6).

The live failure: the goal `create-welcome-popup` has four believed success
signals. On a real run the agent confirmed all four facts IN their declared
type, but reported them with CONCRETE per-run instance tokens (the real
campaign id, the full hostnames, the real campaign name) while the seed used
ABSTRACT placeholders. Per-type Jaccard fell below 0.5 on three of four
(url/text/network), so only the behavioral signal matched, and the genuinely
passing goal read UNCERTAIN -> a false REGRESSED.

With structured `value_predicate`s the invariant is matched EXACTLY and only
the declared instance slot is tolerant, so the three formerly-missed signals
match by construction. The no-false-pass tests prove the structured path is
STRICTER than Jaccard: a wrong status code, a wrong route, a failure invariant,
or an unfilled slot still does NOT match.
"""
from __future__ import annotations

from datetime import datetime, timezone

from praxis.model import (
    KnowledgeFile,
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
        last_verified=datetime(2026, 6, 8, tzinfo=timezone.utc),
        observation_count=1,
    )


def _seed(type_: SignalType, value: str, predicate: str) -> Signal:
    return Signal(type=type_, value=value, value_predicate=predicate,
                  provenance=_prov(), confidence=1.0, status=Status.BELIEVED)


def _obs(kind: str, type_: SignalType, value: str) -> ObservedSignal:
    return ObservedSignal(kind=kind, type=type_, value=value,
                          source_type=SourceType.AGENT, source_id="regress-agent")


# The three live-failing signals as structured seeds (ADR-0030 decision 1).
_URL_SEED = _seed(
    SignalType.URL,
    "the editor route for the just-created campaign",
    "the route matches /Box/Editor/{campaign_id:numeric}",
)
_TEXT_SEED = _seed(
    SignalType.TEXT,
    "a banner names the created campaign",
    "a banner whose text contains Created Campaign {campaign_id}",
)
_NETWORK_SEED = _seed(
    SignalType.NETWORK,
    "the create call returns 2xx and the new row appears",
    "GET account.digioh.com/ returns 2xx and the campaign list contains a row "
    "whose id equals {campaign_id}",
)


# --- Step 5: the three formerly-missed signals now match --------------------


def test_url_signal_matches_concrete_instance() -> None:
    # seed slot {campaign_id:numeric}; observed concrete real id.
    assert _value_matches(
        _obs("success", SignalType.URL, "the route matches /Box/Editor/329419"),
        _URL_SEED,
    )


def test_text_signal_matches_concrete_instance() -> None:
    assert _value_matches(
        _obs("success", SignalType.TEXT,
             "a banner whose text contains Created Campaign 329419"),
        _TEXT_SEED,
    )


def test_network_signal_matches_concrete_instance() -> None:
    assert _value_matches(
        _obs("success", SignalType.NETWORK,
             "GET account.digioh.com/ returns 2xx and the campaign list "
             "contains a row whose id equals 329419"),
        _NETWORK_SEED,
    )


def test_create_welcome_popup_goal_passes_end_to_end() -> None:
    """All four believed success signals match -> PASS (no more false
    UNCERTAIN). The behavioral signal stays free-text (only three were
    re-seeded structurally), proving the two paths coexist (decision 4)."""
    behavioral = Signal(
        type=SignalType.BEHAVIORAL,
        value="a welcome popup is created and appears in the campaign list",
        provenance=_prov(), confidence=1.0, status=Status.BELIEVED,
    )
    kf = KnowledgeFile(
        schema_version="0", goal_id="create-welcome-popup",
        goal="a user can create a welcome popup",
        target=Target(app="digioh"),
        success_signals=[behavioral, _URL_SEED, _TEXT_SEED, _NETWORK_SEED],
        meta=Meta(created_at=datetime(2026, 6, 8, tzinfo=timezone.utc),
                  updated_at=datetime(2026, 6, 8, tzinfo=timezone.utc)),
    )
    obs = [
        _obs("success", SignalType.BEHAVIORAL,
             "a welcome popup is created and appears in the campaign list"),
        _obs("success", SignalType.URL, "the route matches /Box/Editor/329419"),
        _obs("success", SignalType.TEXT,
             "a banner whose text contains Created Campaign 329419"),
        _obs("success", SignalType.NETWORK,
             "GET account.digioh.com/ returns 2xx and the campaign list "
             "contains a row whose id equals 329419"),
    ]
    verdict, matched, _ = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.PASS
    assert len(matched) == 4


# --- Step 5/6: the structured path is STRICTER, never looser ----------------


def test_wrong_status_code_does_not_match_network() -> None:
    # `returns 500` cannot satisfy a `returns 2xx` invariant; 0.5 Jaccard on the
    # many shared words could have admitted it.
    assert not _value_matches(
        _obs("success", SignalType.NETWORK,
             "GET account.digioh.com/ returns 500 and the campaign list "
             "contains a row whose id equals 329419"),
        _NETWORK_SEED,
    )


def test_wrong_route_does_not_match_url() -> None:
    assert not _value_matches(
        _obs("success", SignalType.URL, "the route matches /Account/Login/329419"),
        _URL_SEED,
    )


def test_non_numeric_route_segment_does_not_match_url() -> None:
    # A non-numeric route segment is itself a regression (shape guard).
    assert not _value_matches(
        _obs("success", SignalType.URL, "the route matches /Box/Editor/login"),
        _URL_SEED,
    )


def test_wrong_type_never_matches_even_with_holding_invariant() -> None:
    # Exact-type equality still gates first (ADR-0028): a behavioral observation
    # of the url invariant does not match the url seed.
    assert not _value_matches(
        _obs("success", SignalType.BEHAVIORAL,
             "the route matches /Box/Editor/329419"),
        _URL_SEED,
    )
