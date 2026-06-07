"""Multi-writer concurrency contract (ADR-0012).

Covers the store-layer contract (no lost events, per-tenant layout), the
gate-layer contract (`source_id = agent_identity`; N same-model writers
collapse to one source under ADR-0008), and the partial-write resilience
clause (a leftover `*.tmp` file is ignored by readers, never surfaced as a
real event).
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from mneme.merge import project
from mneme.model import Target
from mneme.store import (
    DEFAULT_TENANT_ID,
    FORBIDDEN_SOURCE_TOKEN_KINDS,
    AgentIdentity,
    FileEventStore,
    ObservationEvent,
    ObservedSignal,
    source_id_for,
)


NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


# ---------- Section 1: store-layer contract ----------------------------------


def _sig(value: str, type_: str = "behavioral", src_id: str = "m::p") -> ObservedSignal:
    return ObservedSignal(kind="success", type=type_, value=value,
                          source_type="agent", source_id=src_id,
                          observed_app_version="1")


def test_tenant_root_layout(tmp_path) -> None:
    """Events MUST land at `<root>/<tenant_id>/events/`. The path is the
    Phase 2 placeholder for the Phase 3 RBAC boundary."""
    store = FileEventStore(tmp_path, default_tenant_id="tenant-a")
    store.append(ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("x")]))
    events_dir = tmp_path / "tenant-a" / "events"
    assert events_dir.is_dir()
    assert len(list(events_dir.glob("*.json"))) == 1


def test_default_tenant_is_local(tmp_path) -> None:
    """`DEFAULT_TENANT_ID == 'local'` per ADR-0012."""
    assert DEFAULT_TENANT_ID == "local"
    store = FileEventStore(tmp_path)
    store.append(ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("x")]))
    assert (tmp_path / "local" / "events").is_dir()


def test_per_call_tenant_id_overrides_default(tmp_path) -> None:
    """Per-call `tenant_id` scopes BOTH reads and writes; the SPI has no
    cross-tenant read surface (ADR-0012 section 3)."""
    store = FileEventStore(tmp_path, default_tenant_id="alpha")
    store.append(
        ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("alpha-1")]),
        tenant_id="alpha",
    )
    store.append(
        ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("beta-1")]),
        tenant_id="beta",
    )
    # Each tenant sees only its own events. There is no read across tenants.
    assert {e.signals[0].value for e in store.read(tenant_id="alpha")} == {"alpha-1"}
    assert {e.signals[0].value for e in store.read(tenant_id="beta")} == {"beta-1"}


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "a\\b", "a\x00b"])
def test_path_traversal_tenant_ids_rejected(tmp_path, bad: str) -> None:
    """Tenant ids that would let a write land outside `<root>/<tenant_id>/`
    are rejected at the boundary."""
    store = FileEventStore(tmp_path)
    with pytest.raises(ValueError):
        store.append(
            ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("x")]),
            tenant_id=bad,
        )


def test_concurrent_appends_no_lost_events(tmp_path) -> None:
    """Many writers across two distinct agent identities all land. The
    file_store loses NOTHING; the projection retains BOTH evidence types."""
    store = FileEventStore(tmp_path)
    n = 40

    def writer(model: str, type_: str) -> None:
        src = source_id_for(model=model, prompt_lineage="phase-2-prompt-v1")
        for i in range(n):
            store.append(ObservationEvent(
                agent_id=src, goal_id="g",
                signals=[ObservedSignal(
                    kind="success", type=type_, value=f"{type_} signal seen",
                    source_type="agent", source_id=src, observed_app_version="1",
                )],
            ))

    threads = [
        threading.Thread(target=writer, args=("model-a", "behavioral")),
        threading.Thread(target=writer, args=("model-b", "network")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = store.read("g")
    assert len(events) == 2 * n  # nothing lost

    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    # Both distinct agent identities are kept; the projection counts them as
    # two independent sources and promotes via diversity.
    assert {s.status.value for s in kf.success_signals} == {"believed"}


# ---------- Section 2: gate-layer contract -----------------------------------


def test_source_id_for_model_plus_prompt_lineage() -> None:
    sid = source_id_for(model="claude-haiku-3", prompt_lineage="phase-2-r-mode-v1")
    assert sid == "claude-haiku-3::phase-2-r-mode-v1"


@pytest.mark.parametrize("model,lineage", [
    ("", "p"),
    ("m", ""),
    ("m::oops", "p"),
    ("m", "p::oops"),
])
def test_agent_identity_rejects_empty_or_reserved_separator(model: str, lineage: str) -> None:
    with pytest.raises(ValueError):
        AgentIdentity(model=model, prompt_lineage=lineage)


def test_forbidden_source_token_kinds_listed() -> None:
    """The set is exposed so the harness and tests can assert that no writer
    is reaching for a per-process token (ADR-0012)."""
    for kind in ("pid", "session_id", "run_uuid", "hostname"):
        assert kind in FORBIDDEN_SOURCE_TOKEN_KINDS


def test_n_concurrent_same_model_writers_count_as_one_source(tmp_path) -> None:
    """ADR-0012 section 2: `source_id = agent_identity` so N parallel writers
    of the same model collapse to one source. Even with many events, the
    diversity-or-seed gate is NOT satisfied (same type, same source)."""
    store = FileEventStore(tmp_path)
    src = source_id_for(model="claude-sonnet", prompt_lineage="r-mode-v1")
    n_writers = 5
    per_writer = 10

    def writer(_i: int) -> None:
        for _j in range(per_writer):
            store.append(ObservationEvent(
                agent_id=src, goal_id="g",
                signals=[ObservedSignal(
                    kind="success", type="behavioral",
                    value="logout action becomes available",
                    source_type="agent", source_id=src, observed_app_version="1",
                )],
            ))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = store.read("g")
    assert len(events) == n_writers * per_writer  # no lost events

    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    # Same-type, same-source -> projection MUST NOT promote to believed.
    # Without diversity-or-seed, the only honest verdict is `contested`.
    statuses = {s.status.value for s in kf.success_signals}
    assert statuses == {"contested"}, statuses


def test_racing_contradiction_yields_contested(tmp_path) -> None:
    """Two distinct agent identities race; one reports a failure signal
    present, the other reports it absent. The projection MUST surface the
    disagreement as `contested`, not last-write-wins."""
    store = FileEventStore(tmp_path)
    sid_a = source_id_for(model="model-a", prompt_lineage="p")
    sid_b = source_id_for(model="model-b", prompt_lineage="p")

    # A success signal so the projection is valid.
    store.append(ObservationEvent(
        agent_id=sid_a, goal_id="g",
        signals=[ObservedSignal(kind="success", type="behavioral",
                                value="logout ok", source_type="agent",
                                source_id=sid_a, observed_app_version="1")],
    ))

    def write(sid: str, present: bool) -> None:
        store.append(ObservationEvent(
            agent_id=sid, goal_id="g",
            signals=[ObservedSignal(
                kind="failure", type="behavioral", value="captcha appears",
                present=present, source_type="agent", source_id=sid,
                observed_app_version="1",
            )],
        ))

    threads = [
        threading.Thread(target=write, args=(sid_a, True)),
        threading.Thread(target=write, args=(sid_b, False)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = store.read("g")
    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    captcha = next(s for s in (kf.failure_signals or []) if s.value == "captcha appears")
    assert captcha.status.value == "contested"


def test_racing_oscillation_quarantines(tmp_path) -> None:
    """Oscillation across writers MUST surface as `quarantined` per ADR-0005."""
    store = FileEventStore(tmp_path)
    sid_a = source_id_for(model="model-a", prompt_lineage="p")
    sid_b = source_id_for(model="model-b", prompt_lineage="p")

    # Three observations in known time order, alternating presence.
    base = NOW - timedelta(days=2)
    rows = [
        (sid_a, True, base),
        (sid_b, False, base + timedelta(hours=1)),
        (sid_a, True, base + timedelta(hours=2)),
    ]
    for sid, present, ts in rows:
        store.append(ObservationEvent(
            agent_id=sid, goal_id="g", ts=ts,
            signals=[ObservedSignal(
                kind="success", type="behavioral", value="flaky button",
                present=present, source_type="agent", source_id=sid,
                observed_app_version="1",
            )],
        ))

    events = store.read("g")
    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    flaky = next(s for s in kf.success_signals if s.value == "flaky button")
    assert flaky.status.value == "quarantined"


# ---------- Section 3: partial-write resilience ------------------------------


def test_leftover_tmp_file_is_ignored_by_readers(tmp_path) -> None:
    """A process that died between the `.tmp` write and the `.rename` leaves
    a dotfile behind. Readers MUST NOT pick it up as a real event; the
    `*.json` glob skips dotfiles by construction."""
    store = FileEventStore(tmp_path)
    # Write one legitimate event so the events dir exists.
    store.append(ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("real")]))
    events_dir = tmp_path / "local" / "events"
    # Simulate a crashed writer: leftover tmp file with garbage.
    (events_dir / ".garbage.tmp").write_text("partial-write-not-json", encoding="utf-8")
    # The reader still sees ONLY the real event.
    seen = store.read("g")
    assert len(seen) == 1
    assert seen[0].signals[0].value == "real"


def test_cross_tenant_writes_do_not_leak(tmp_path) -> None:
    """A read scoped to tenant A never observes events written under tenant
    B, even when they share the same root. Cross-tenant visibility is not
    representable on this SPI (ADR-0012 section 3)."""
    store = FileEventStore(tmp_path, default_tenant_id="a")
    store.append(
        ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("a-only")]),
        tenant_id="a",
    )
    store.append(
        ObservationEvent(agent_id="m::p", goal_id="g", signals=[_sig("b-only")]),
        tenant_id="b",
    )
    assert {e.signals[0].value for e in store.read(tenant_id="a")} == {"a-only"}
    assert {e.signals[0].value for e in store.read(tenant_id="b")} == {"b-only"}
