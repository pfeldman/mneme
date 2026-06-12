"""Recency decay (ADR-0013).

Decay is a property of the PROJECTION over the immutable event log: confidence
drift is pure derivation; status flips emit explicit `DecayEvent`s. The store
is never mutated.

The mandatory test cases from the ADR-0013 contract:
  1. decay-then-fresh-signal-restores: a `believed` oracle whose evidence ages
     past the version anchor flips to `stale`; a fresh independent-diverse pair
     of observations restores it to `believed` via the ADR-0008 cold-start gate.
  2. decay-does-not-resolve-contested-by-aging-one-side: a `contested` signal
     (positive vs negative disagreement) does NOT silently become `believed` by
     aging out one half of the contradiction.
  3. same-type-repeats-cannot-keep-believed-alive: a `believed` oracle that
     loses its diverse signal to decay cannot be kept alive by stacking same-
     type same-source observations at the current version.

Plus coverage of:
  - Multi-writer anchor selection (highest-semver-wins when caller passes None).
  - Unidirectional decay (a retired observation does not rejoin the surviving
    set on its own; re-promotion requires fresh diverse evidence).
  - Status flip events carry retired event ids, the anchor used, and the rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from praxis.merge import (
    DecayConfig,
    evaluate_decay,
    project_with_decay,
    select_current_version,
)
from praxis.merge.decay import is_observation_staled
from praxis.model import Target
from praxis.store import ObservationEvent, ObservedSignal

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _obs(
    value: str,
    type_: str = "behavioral",
    *,
    present: bool = True,
    src_id: str = "a1",
    src_type: str = "agent",
    ver: str | None = "1.0.0",
    kind: str = "success",
) -> ObservedSignal:
    return ObservedSignal(
        kind=kind,  # type: ignore[arg-type]
        type=type_,  # type: ignore[arg-type]
        value=value,
        present=present,
        source_type=src_type,  # type: ignore[arg-type]
        source_id=src_id,
        observed_app_version=ver,
    )


def _ev(*signals: ObservedSignal, ts: datetime = NOW, agent: str = "a1",
        goal: str = "g", ver: str | None = "1.0.0") -> ObservationEvent:
    return ObservationEvent(
        agent_id=agent, goal_id=goal, ts=ts, observed_app_version=ver,
        signals=list(signals),
    )


def _proj(events, **kw):
    return project_with_decay(
        events,
        goal_id="g",
        goal="auth",
        target=Target(app="acme"),
        now=kw.pop("now", NOW),
        current_version=kw.pop("current_version", "1.0.0"),
        decay_config=kw.pop("decay_config", DecayConfig()),
        **kw,
    )


# ---------------------------------------------------------------- anchor selection


def test_select_current_version_prefers_caller() -> None:
    obs = [_obs("x", ver="9.0.0"), _obs("y", ver="2.0.0")]
    assert select_current_version(caller_supplied="1.0.0", observations=obs) == "1.0.0"


def test_select_current_version_picks_highest_semver_across_writers() -> None:
    """Multi-writer: two writers record different versions for the same logical
    signal. Highest-semver wins, write-order-independent (ADR-0013 section 5)."""
    obs_a = [_obs("x", src_id="writer_a", ver="1.4.0")]
    obs_b = [_obs("x", src_id="writer_b", ver="1.2.0")]
    # Either order gives the same anchor.
    assert select_current_version(caller_supplied=None, observations=obs_a + obs_b) == "1.4.0"
    assert select_current_version(caller_supplied=None, observations=obs_b + obs_a) == "1.4.0"


def test_select_current_version_returns_none_when_no_semver() -> None:
    obs = [_obs("x", ver="latest"), _obs("y", ver=None)]
    assert select_current_version(caller_supplied=None, observations=obs) is None


# ---------------------------------------------------------------- per-obs staleness


def test_is_observation_staled_version_minor_back() -> None:
    cfg = DecayConfig(minor_versions_back=2)
    # 1.4.0 - 1.1.0 = 3 minors back -> staled by version anchor.
    staled, rule = is_observation_staled(
        obs_version="1.1.0", obs_ts=NOW, current_version="1.4.0", now=NOW, config=cfg,
    )
    assert staled is True
    assert rule == "version"


def test_is_observation_staled_version_within_window() -> None:
    cfg = DecayConfig(minor_versions_back=2)
    # 1.4.0 - 1.2.0 = 2 minors back -> within window, NOT staled.
    staled, _ = is_observation_staled(
        obs_version="1.2.0", obs_ts=NOW, current_version="1.4.0", now=NOW, config=cfg,
    )
    assert staled is False


def test_is_observation_staled_major_bump_stales_all() -> None:
    cfg = DecayConfig()
    staled, rule = is_observation_staled(
        obs_version="1.9.0", obs_ts=NOW, current_version="2.0.0", now=NOW, config=cfg,
    )
    assert staled is True
    assert rule == "version"


def test_is_observation_staled_wallclock() -> None:
    cfg = DecayConfig(stale_after_days=90.0)
    staled, rule = is_observation_staled(
        obs_version="1.0.0", obs_ts=NOW - timedelta(days=120),
        current_version="1.0.0", now=NOW, config=cfg,
    )
    assert staled is True
    assert rule == "wallclock"


def test_is_observation_staled_both_anchors_fire() -> None:
    cfg = DecayConfig(minor_versions_back=2, stale_after_days=30.0)
    staled, rule = is_observation_staled(
        obs_version="1.0.0", obs_ts=NOW - timedelta(days=60),
        current_version="1.4.0", now=NOW, config=cfg,
    )
    assert staled is True
    assert rule == "both"


# ---------------------------------------------------------------- the ADR contract


def test_decay_then_fresh_signal_restores_via_cold_start_gate() -> None:
    """A `believed` oracle whose evidence ages past the version anchor flips to
    `stale`; a fresh independent-diverse pair restores `believed` (ADR-0008).
    Re-promotion does NOT happen by aging; it happens because the fresh
    evidence on its own passes the cold-start gate."""
    # Old believed: behavioral (a1) + network (a2) at 1.0.0
    old = [
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.0.0")),
        _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.0.0")),
    ]
    # Test at current_version 1.4.0 (3 minors back -> staled).
    kf_stale, decay_events = _proj(old, current_version="1.4.0")
    statuses = {s.status.value for s in kf_stale.success_signals}
    assert "stale" in statuses
    assert decay_events  # status flip emitted at least one decay event
    assert all(de.to_status == "stale" for de in decay_events)
    assert all(de.rule in ("version", "both") for de in decay_events)

    # Now add fresh independent-diverse evidence at 1.4.0; the projection
    # re-promotes to `believed` via the cold-start gate (not by un-staleing
    # the retired events).
    fresh = old + [
        _ev(_obs("logout", "behavioral", src_id="a3", ver="1.4.0"),
            ts=NOW),
        _ev(_obs("POST /session 2xx", "network", src_id="a4", ver="1.4.0"),
            ts=NOW),
    ]
    kf_restored, _ = _proj(fresh, current_version="1.4.0",
                            prior_decay_events=decay_events)
    statuses_now = {(s.type.value, s.value): s.status.value
                    for s in kf_restored.success_signals}
    assert statuses_now[("behavioral", "logout")] == "believed"
    assert statuses_now[("network", "POST /session 2xx")] == "believed"


def test_decay_does_not_resolve_contested_by_aging_one_side() -> None:
    """A signal seen present THEN explicitly NOT seen is `contested`. Aging out
    one half of the disagreement must NOT silently flip it to `believed`."""
    events = [
        _ev(_obs("captcha appears", "behavioral", present=True, kind="failure",
                  src_id="a1", ver="1.0.0"),
            ts=NOW - timedelta(days=200), ver="1.0.0"),
        _ev(_obs("captcha appears", "behavioral", present=False, kind="failure",
                  src_id="a2", ver="1.4.0"),
            ts=NOW, ver="1.4.0"),
        # Add a believed success so the projection is valid.
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.4.0"),
            ts=NOW, ver="1.4.0"),
        _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.4.0"),
            ts=NOW, ver="1.4.0"),
    ]
    kf, decay_events = _proj(events, current_version="1.4.0")
    assert kf.failure_signals is not None
    captcha = next(s for s in kf.failure_signals if s.value == "captcha appears")
    # Either contested (if both sides are alive after decay) or stale (if the
    # decay flip retired the old positive and the survivor turned the row to
    # stale). NEVER `believed`.
    assert captcha.status.value in ("contested", "stale")
    assert captcha.status.value != "believed"
    # No decay event should record `from_status="believed"` for this signal.
    for de in decay_events:
        if (de.signal_kind, de.signal_value) == ("failure", "captcha appears"):
            assert de.from_status != "believed"


def test_same_type_repeats_cannot_keep_believed_alive() -> None:
    """A `believed` oracle that loses its diverse signal to decay cannot be
    kept alive by stacking same-type same-source observations at the current
    version (ADR-0013 section 2 + ADR-0008 source-independence)."""
    # Old diverse pair: behavioral (a1) at 1.0.0 + network (a2) at 1.0.0
    # Then same-source same-type repeats at 1.4.0.
    events = [
        # The old diverse pair (decays at 1.4.0).
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
        _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
        # Same-type same-source attempts to refresh `behavioral`.
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.4.0"),
            ts=NOW, ver="1.4.0"),
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.4.0"),
            ts=NOW, ver="1.4.0"),
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.4.0"),
            ts=NOW, ver="1.4.0"),
    ]
    kf, decay_events = _proj(events, current_version="1.4.0")
    # The lone behavioral type from a single source cannot pass
    # `independent_diverse` over the surviving set: needs >=2 types AND >=2
    # source_ids.
    statuses = {(s.type.value, s.value): s.status.value
                for s in kf.success_signals}
    assert statuses[("behavioral", "logout")] != "believed"
    # The diverse `network` evidence has aged out and was not refreshed.
    assert statuses.get(("network", "POST /session 2xx")) in ("stale", None)
    # And a decay event was emitted recording the flip from believed.
    assert any(
        de.from_status == "believed" and de.to_status == "stale"
        for de in decay_events
    )


# ---------------------------------------------------------------- unidirectional


def test_decay_is_unidirectional_retired_obs_does_not_unstale() -> None:
    """Once a `DecayEvent` retires an observation, that observation cannot
    rejoin the surviving set just because a later same-type same-source obs
    lands fresh. Re-promotion requires fresh DIVERSE evidence."""
    # Old pair, then decayed.
    old = [
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
        _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
    ]
    _, first_decay = _proj(old, current_version="1.4.0")
    assert first_decay, "decay events should have been emitted"

    # Now add same-type repeats from the SAME sources. Without fresh diversity,
    # the cold-start gate cannot re-promote.
    same_type_only = old + [
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.4.0"),
            ts=NOW, ver="1.4.0"),
    ]
    kf, _ = _proj(same_type_only, current_version="1.4.0",
                  prior_decay_events=first_decay)
    statuses = {(s.type.value, s.value): s.status.value
                for s in kf.success_signals}
    # No fresh diverse pair -> the live signal alone cannot resurrect believed.
    assert statuses[("behavioral", "logout")] != "believed"


# ---------------------------------------------------------------- audit trail


def test_decay_event_carries_retired_event_ids_and_anchor() -> None:
    """Every decay event must reference the retired event ids, the anchor
    used, and the rule that fired (ADR-0013 section 1, loud-and-traceable)."""
    e1 = _ev(_obs("logout", "behavioral", src_id="a1", ver="1.0.0"),
             ts=NOW - timedelta(days=30), ver="1.0.0")
    e2 = _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.0.0"),
             ts=NOW - timedelta(days=30), ver="1.0.0")
    _, decay_events = _proj([e1, e2], current_version="1.4.0")
    assert decay_events
    for de in decay_events:
        assert de.retired_event_ids
        assert de.anchor_current_version == "1.4.0"
        assert de.anchor_now == NOW
        assert de.rule in ("version", "wallclock", "both")
        assert de.note  # human-readable trace


def test_no_decay_event_when_no_status_flip() -> None:
    """Pure confidence drift between projections does NOT emit a decay event
    (ADR-0013 section 1: confidence shifts are pure derivation)."""
    # Fresh diverse pair at the current version - no decay should fire.
    events = [
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.4.0"), ver="1.4.0"),
        _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.4.0"), ver="1.4.0"),
    ]
    _, decay_events = _proj(events, current_version="1.4.0")
    assert decay_events == []


def test_decay_collision_anchor_is_write_order_independent() -> None:
    """Two writers, two versions; the projection picks the highest-semver
    anchor independent of write order. The flip set is identical."""
    a = _ev(_obs("logout", "behavioral", src_id="a1", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0", agent="a1")
    b = _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.4.0"),
            ts=NOW, ver="1.4.0", agent="a2")
    # When caller passes nothing, anchor = highest semver (1.4.0).
    kf1, dec1 = project_with_decay(
        [a, b],
        goal_id="g", goal="auth", target=Target(app="acme"),
        now=NOW, current_version=None,
    )
    kf2, dec2 = project_with_decay(
        [b, a],
        goal_id="g", goal="auth", target=Target(app="acme"),
        now=NOW, current_version=None,
    )
    statuses1 = {(s.type.value, s.value): s.status.value for s in kf1.success_signals}
    statuses2 = {(s.type.value, s.value): s.status.value for s in kf2.success_signals}
    assert statuses1 == statuses2
    assert {de.signal_value for de in dec1} == {de.signal_value for de in dec2}


# ---------------------------------------------------------------- ADR-0035 partition


def test_dev2_ahead_of_prod_version_bump_cannot_stale_prod_evidence(tmp_path) -> None:
    """The ADR-0035 decay collision, fixed by partitioning (decision 5 /
    analysis section 4.1): dev2 runs 2.6.0, prod runs 2.3.0, N=2 minors.

    Folded (the pre-ADR-0035 failure mode), the anchor derives from the
    highest semver across BOTH deployments (2.6.0), so prod's fresh 2.3.0
    evidence lands 3 minors back and stales - a healthy prod decayed by a
    dev2 deploy. Partitioned at the adapter read path, prod's anchor derives
    from prod's OWN supporting set (2.3.0), nothing stales, and `decay.py`
    itself is unchanged."""
    from praxis.adapters import BrowserUseAdapter
    from praxis.store import FileEventStore

    store = FileEventStore(tmp_path)
    target = Target(app="acme")
    prod = BrowserUseAdapter(store, target=target, environment="prod",
                             current_version="2.3.0")
    dev2 = BrowserUseAdapter(store, target=target, environment="dev2",
                             current_version="2.6.0")
    # Prod earned believed: behavioral (a1) + network (a2), both fresh at 2.3.0.
    prod.write_observations(
        "g", "a1", [_obs("logout", "behavioral", src_id="a1", ver="2.3.0")])
    prod.write_observations(
        "g", "a2", [_obs("POST /session 2xx", "network", src_id="a2", ver="2.3.0")])
    # A dev2 run observes the goal on the deployment that ships ahead.
    dev2.write_observations(
        "g", "a1", [_obs("logout", "behavioral", src_id="a1", ver="2.6.0")])

    # The FOLDED stream is the failure mode the partition exists to kill:
    # anchor 2.6.0 stales prod's day-fresh evidence and flips its belief.
    _, folded_decay = project_with_decay(
        store.read("g"), goal_id="g", goal="auth", target=target,
        current_version=None,
    )
    assert any(de.signal_value == "POST /session 2xx" for de in folded_decay)

    # PARTITIONED (what the prod adapter actually feeds the projection):
    # prod's own stream only -> anchor 2.3.0 -> no flip, belief intact.
    prod_events = prod.read_events("g")
    assert {e.environment for e in prod_events} == {"prod"}
    kf, decay_events = project_with_decay(
        prod_events, goal_id="g", goal="auth", target=target,
        current_version=None,
    )
    assert decay_events == []
    statuses = {(s.type.value, s.value): s.status.value
                for s in kf.success_signals}
    assert statuses[("behavioral", "logout")] == "believed"
    assert statuses[("network", "POST /session 2xx")] == "believed"


def test_decay_event_replay_is_partitioned_by_environment(tmp_path) -> None:
    """A decay flip recorded for one environment must not retire another
    environment's observations on replay: `read_decay_events` filters the
    prior-decay input with the same partition rule as the event stream
    (ADR-0035 decision 4), so replaying one env's log reconstructs that
    env's flips without touching the other's."""
    from praxis.adapters import BrowserUseAdapter
    from praxis.store import DecayEvent, FileEventStore

    store = FileEventStore(tmp_path)
    target = Target(app="acme")
    prod = BrowserUseAdapter(store, target=target, environment="prod")
    dev2_flip = DecayEvent(
        goal_id="g", environment="dev2",
        signal_kind="success", signal_type="network",
        signal_value="POST /session 2xx",
        from_status="believed", to_status="stale",
        retired_event_ids=["e1"], anchor_current_version="2.6.0",
        anchor_now=NOW, rule="version",
    )
    store.append_decay(dev2_flip)
    # The dev2 flip is invisible to prod's partition...
    assert prod.read_decay_events("g") == []
    # ...and visible to dev2's and to an undeclared (unfiltered) read.
    dev2 = BrowserUseAdapter(store, target=target, environment="dev2")
    unfiltered = BrowserUseAdapter(store, target=target)
    assert [de.event_id for de in dev2.read_decay_events("g")] \
        == [dev2_flip.event_id]
    assert [de.event_id for de in unfiltered.read_decay_events("g")] \
        == [dev2_flip.event_id]


# ---------------------------------------------------------------- evaluate_decay pure


def test_evaluate_decay_is_pure_no_store_interaction() -> None:
    """`evaluate_decay` is a pure derivation: same inputs, same outputs. No
    store mutation; the caller decides when to append."""
    events = [
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
        _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
    ]
    e1 = evaluate_decay(
        events=events, goal_id="g", current_version="1.4.0", now=NOW,
        config=DecayConfig(),
    )
    e2 = evaluate_decay(
        events=events, goal_id="g", current_version="1.4.0", now=NOW,
        config=DecayConfig(),
    )
    keys1 = sorted((de.signal_kind, de.signal_value) for de in e1.new_decay_events)
    keys2 = sorted((de.signal_kind, de.signal_value) for de in e2.new_decay_events)
    assert keys1 == keys2
    assert e1.staled_signal_keys == e2.staled_signal_keys


def test_decay_event_is_storable(tmp_path) -> None:
    """End-to-end: the decay event the projection emits round-trips through
    the file store (append-only, sibling subdir)."""
    from praxis.store import FileEventStore
    store = FileEventStore(tmp_path)
    events = [
        _ev(_obs("logout", "behavioral", src_id="a1", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
        _ev(_obs("POST /session 2xx", "network", src_id="a2", ver="1.0.0"),
            ts=NOW - timedelta(days=30), ver="1.0.0"),
    ]
    for ev in events:
        store.append(ev)
    _, decay_events = project_with_decay(
        store.read("g"),
        goal_id="g", goal="auth", target=Target(app="acme"),
        now=NOW, current_version="1.4.0",
    )
    assert decay_events
    for de in decay_events:
        store.append_decay(de)
    # Round-trip.
    read_back = store.read_decay("g")
    assert {de.event_id for de in read_back} == {de.event_id for de in decay_events}
    # Append-only: cannot overwrite.
    import pytest
    with pytest.raises(FileExistsError):
        store.append_decay(decay_events[0])
