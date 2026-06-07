"""Adapters are the only runtime code and the redaction boundary (ADR-0003, docs/06).
They store knowledge (oracles), never procedures."""
from __future__ import annotations

from praxis.adapters import BrowserUseAdapter, KnowledgeAdapter, redact
from praxis.model import (
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    Target,
)
from praxis.store import FileEventStore, ObservedSignal


def test_redaction_strips_secrets_and_pii() -> None:
    assert "@" not in redact("login as alice@example.com")
    assert "hunter2" not in redact("password=hunter2")
    assert redact("card 4111111111111111") == "card <card-number>"
    out = redact("token eyJhbGciOi.JzdWIiOiJ.SflKxwRJ")
    assert "eyJ" not in out


def _seed() -> KnowledgeFile:
    import datetime as dt
    now = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    return KnowledgeFile(
        schema_version="0", goal_id="authenticate-user", goal="A user can authenticate.",
        target=Target(app="acme", observed_app_versions=["1"]),
        success_signals=[Signal(
            type="behavioral", value="a logout action becomes available",
            provenance=Provenance(source_type="spec", source_id="AC-1",
                                  observed_app_version="1", last_verified=now,
                                  observation_count=1),
            confidence=1.0, status="believed")],
        meta=Meta(created_at=now, updated_at=now),
    )


def test_adapter_satisfies_spi(tmp_path) -> None:
    adapter = BrowserUseAdapter(FileEventStore(tmp_path), target=Target(app="acme"),
                                seeds={"authenticate-user": _seed()}, current_version="1")
    assert isinstance(adapter, KnowledgeAdapter)  # runtime Protocol check


def test_write_then_read_round_trip_and_redacts(tmp_path) -> None:
    store = FileEventStore(tmp_path)
    adapter = BrowserUseAdapter(store, target=Target(app="acme"),
                                seeds={"authenticate-user": _seed()}, current_version="1")
    adapter.write_observations(
        "authenticate-user", "explorer-1",
        [ObservedSignal(kind="success", type="network",
                        value="POST /session 2xx for user@x.com", present=True,
                        source_type="agent", source_id="explorer-1",
                        observed_app_version="1")],
        observed_app_version="1",
    )
    # The stored event must be redacted at the boundary.
    stored = store.read("authenticate-user")[0]
    assert "@x.com" not in stored.signals[0].value
    # Seed (behavioral) + agent (network) → diversity → believed knowledge.
    kf = adapter.read_knowledge("authenticate-user")
    assert kf is not None
    assert {s.status.value for s in kf.success_signals} == {"believed"}


def test_read_unknown_goal_is_none(tmp_path) -> None:
    adapter = BrowserUseAdapter(FileEventStore(tmp_path), target=Target(app="acme"))
    assert adapter.read_knowledge("nope") is None


def test_prompt_contains_goal_and_oracles_but_no_steps(tmp_path) -> None:
    adapter = BrowserUseAdapter(FileEventStore(tmp_path), target=Target(app="acme"),
                                seeds={"authenticate-user": _seed()}, current_version="1")
    prompt = adapter.build_agent_task("authenticate-user")
    assert "logout action" in prompt
    assert "decide the steps yourself" in prompt.lower()
    # It must NOT contain selectors/coordinates/recorded steps.
    for forbidden in ("click(", "css=", "xpath", "#email", "selector"):
        assert forbidden not in prompt.lower()


def test_build_agent_without_extra_raises_clear_error(tmp_path) -> None:
    import builtins
    import pytest

    adapter = BrowserUseAdapter(FileEventStore(tmp_path), target=Target(app="acme"),
                                seeds={"authenticate-user": _seed()}, current_version="1")
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "browser_use":
            raise ImportError("no browser_use")
        return real_import(name, *a, **k)

    builtins.__import__ = fake_import
    try:
        with pytest.raises(ImportError, match="browser-use"):
            adapter.build_agent("authenticate-user")
    finally:
        builtins.__import__ = real_import
