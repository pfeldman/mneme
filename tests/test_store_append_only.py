"""Store is append-only (ADR-0001): no update, no delete, and concurrent writers
never lose knowledge. The last test is the one AGENTS.md requires for any change
that touches store/projection."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from praxis.merge import project
from praxis.model import Target
from praxis.store import (
    FileEventStore,
    ObservationEvent,
    ObservedSignal,
    RegressObservationEvent,
)


def _sig(value: str, type_: str = "behavioral") -> ObservedSignal:
    return ObservedSignal(kind="success", type=type_, value=value,
                          source_type="agent", source_id="a", observed_app_version="1")


def test_append_then_read(tmp_path) -> None:
    store = FileEventStore(tmp_path)
    ev = ObservationEvent(agent_id="a", goal_id="g", signals=[_sig("logout available")])
    store.append(ev)
    got = store.read("g")
    assert len(got) == 1 and got[0].event_id == ev.event_id


def test_no_update_or_delete_api() -> None:
    # The interface intentionally exposes only append/read/since.
    assert not hasattr(FileEventStore, "update")
    assert not hasattr(FileEventStore, "delete")


def test_one_file_per_event(tmp_path) -> None:
    store = FileEventStore(tmp_path)
    for i in range(5):
        store.append(ObservationEvent(agent_id="a", goal_id="g", signals=[_sig(f"s{i}")]))
    # Per ADR-0012 events live under `<root>/<tenant_id>/events/`; the default
    # tenant id is "local" when none is specified on the constructor.
    assert len(list((tmp_path / "local" / "events").glob("*.json"))) == 5


def test_read_filters_by_goal_and_orders_by_time(tmp_path) -> None:
    store = FileEventStore(tmp_path)
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store.append(ObservationEvent(agent_id="a", goal_id="g2", ts=base, signals=[_sig("x")]))
    store.append(ObservationEvent(agent_id="a", goal_id="g1", ts=base + timedelta(minutes=2),
                                  signals=[_sig("late")]))
    store.append(ObservationEvent(agent_id="a", goal_id="g1", ts=base + timedelta(minutes=1),
                                  signals=[_sig("early")]))
    g1 = store.read("g1")
    assert [e.signals[0].value for e in g1] == ["early", "late"]
    assert {e.goal_id for e in store.read()} == {"g1", "g2"}


def test_since(tmp_path) -> None:
    store = FileEventStore(tmp_path)
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store.append(ObservationEvent(agent_id="a", goal_id="g", ts=base, signals=[_sig("old")]))
    store.append(ObservationEvent(agent_id="a", goal_id="g", ts=base + timedelta(hours=1),
                                  signals=[_sig("new")]))
    after = store.since(base)
    assert [e.signals[0].value for e in after] == ["new"]


def test_concurrent_writes_do_not_lose_knowledge(tmp_path) -> None:
    """Two agents writing concurrently: every event survives and the projection
    retains BOTH agents' distinct evidence types (no last-write-wins)."""
    store = FileEventStore(tmp_path)
    n = 25

    def writer(agent: str, type_: str) -> None:
        for i in range(n):
            store.append(ObservationEvent(
                agent_id=agent, goal_id="g",
                signals=[ObservedSignal(kind="success", type=type_,
                                        value=f"{type_} signal", source_type="agent",
                                        source_id=agent, observed_app_version="1")],
            ))

    t1 = threading.Thread(target=writer, args=("a1", "behavioral"))
    t2 = threading.Thread(target=writer, args=("a2", "network"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    events = store.read("g")
    assert len(events) == 2 * n  # nothing lost

    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=datetime(2026, 6, 1, tzinfo=timezone.utc), current_version="1")
    values = {s.value for s in kf.success_signals}
    assert values == {"behavioral signal", "network signal"}  # both kept, not collapsed
    # Different evidence types both present: the diversity rule promotes them.
    assert all(s.status.value == "believed" for s in kf.success_signals)


# --- RegressObservationEvent (ADR-0023 decision 4): the non-promotable,
# append-only regress audit record lives in its own sibling subdir and the
# believed projection never reads it.


def _regress_event(value: str, *, verdict: str = "pass") -> RegressObservationEvent:
    return RegressObservationEvent(
        agent_id="a", goal_id="g", verdict=verdict, signals=[_sig(value)],
    )


def test_regress_event_append_then_read(tmp_path) -> None:
    store = FileEventStore(tmp_path)
    ev = _regress_event("logout available")
    store.append_regress(ev)
    got = store.read_regress("g")
    assert len(got) == 1 and got[0].event_id == ev.event_id
    assert got[0].verdict == "pass"


def test_regress_event_overwrite_raises(tmp_path) -> None:
    """Append-only: re-appending the same regress event id raises (ADR-0001)."""
    store = FileEventStore(tmp_path)
    ev = _regress_event("x")
    store.append_regress(ev)
    with pytest.raises(FileExistsError):
        store.append_regress(ev)


def test_regress_events_live_in_their_own_subdir(tmp_path) -> None:
    """Regress records land under `regress/`, NOT `events/`, so the believed
    projection's `events/` glob never folds them into belief (ADR-0029)."""
    store = FileEventStore(tmp_path)
    store.append_regress(_regress_event("a"))
    assert len(list((tmp_path / "local" / "regress").glob("*.json"))) == 1
    # The promotable observation stream is untouched.
    assert not (tmp_path / "local" / "events").exists() \
        or len(list((tmp_path / "local" / "events").glob("*.json"))) == 0
    assert store.read("g") == []


def test_concurrent_regress_writes_do_not_lose_records(tmp_path) -> None:
    """The `--jobs` aggregate path writes regress records concurrently. The
    salted atomic-rename commit keeps every record (lock-free, ADR-0012); none
    is lost or corrupted under concurrency."""
    store = FileEventStore(tmp_path)
    n = 25

    def writer(agent: str) -> None:
        for i in range(n):
            store.append_regress(RegressObservationEvent(
                agent_id=agent, goal_id="g", verdict="pass",
                signals=[_sig(f"{agent}-{i}")],
            ))

    t1 = threading.Thread(target=writer, args=("a1",))
    t2 = threading.Thread(target=writer, args=("a2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(store.read_regress("g")) == 2 * n  # nothing lost
