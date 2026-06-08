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


# --- ADR-0017: adapter-boundary redaction for auth_state observations --------


def test_redact_strips_bearer_tokens() -> None:
    out = redact("Authorization: Bearer eyJabc.def123.signaturePart")
    # Token must be gone; placeholder remains (either <jwt> or <token>).
    assert "eyJabc" not in out
    assert "signaturePart" not in out


def test_redact_strips_cookie_header_values() -> None:
    out = redact("Set-Cookie: session=abcdef0123456789; Path=/")
    assert "abcdef0123456789" not in out
    assert "cookie" in out.lower()


def test_redact_strips_session_id_assignments() -> None:
    out = redact("session_id=deadbeef1234567890abcdef")
    assert "deadbeef" not in out
    assert "session_id" in out.lower()


def test_redact_strips_user_id_assignments() -> None:
    out = redact("user_id=42-user-uuid-blah")
    assert "user-uuid-blah" not in out
    assert "user_id" in out.lower()


def test_redact_keeps_invariant_descriptions_intact() -> None:
    """ADR-0017 sec 3: redaction is the safety net; the discipline is to write
    invariant descriptions, not values. A clean invariant description must
    survive `redact()` substantially unchanged."""
    invariant = "a session cookie is set after a successful POST to /api/users/login"
    out = redact(invariant)
    # We allow small substitutions but the gist must survive.
    assert "session cookie" in out.lower()
    assert "/api/users/login" in out


def test_assert_auth_state_observation_safe_rejects_bearer() -> None:
    """An adapter that tries to persist an auth_state observation carrying a
    bearer credential is loud-rejected at the boundary (ADR-0017 sec 2)."""
    from praxis.adapters import AuthStateLeakError, assert_auth_state_observation_safe
    import pytest

    obs = ObservedSignal(
        kind="success", type="network",
        value="Authorization: Bearer abcdef.ghijkl.mnopqr",
        present=True, source_type="agent", source_id="conduit-1",
    )
    with pytest.raises(AuthStateLeakError):
        assert_auth_state_observation_safe(obs)


def test_assert_auth_state_observation_safe_rejects_cookie_and_session_id() -> None:
    from praxis.adapters import AuthStateLeakError, assert_auth_state_observation_safe
    import pytest

    for bad in (
        "Set-Cookie: token=abc123",
        "Cookie: session=xyz",
        "session_id=deadbeef",
        "sid=abc",
    ):
        obs = ObservedSignal(
            kind="success", type="network",
            value=bad, present=True,
            source_type="agent", source_id="conduit-1",
        )
        with pytest.raises(AuthStateLeakError):
            assert_auth_state_observation_safe(obs)


def test_assert_auth_state_observation_safe_rejects_user_id_and_email() -> None:
    """user_id / account_id / JWT field names must trigger the boundary check.
    Email-as-PII is caught by `redact()` rather than by this validator (per
    the split: `redact` handles values, this validator handles forbidden FIELD
    NAMES like `user_id` that name credentials by intent)."""
    from praxis.adapters import AuthStateLeakError, assert_auth_state_observation_safe
    import pytest

    for bad in (
        "user_id=42",
        "account_id=customer-7",
        "jwt subject is alice",
        "tenant_id=acme",
        "org_id=mindcloud",
        "workspace_id=42",
    ):
        obs = ObservedSignal(
            kind="success", type="network",
            value=bad, present=True,
            source_type="agent", source_id="conduit-1",
        )
        with pytest.raises(AuthStateLeakError):
            assert_auth_state_observation_safe(obs)


def test_assert_auth_state_observation_safe_accepts_invariant_descriptions() -> None:
    """Posture descriptions ('session cookie is set', '/api/user returns 200')
    are exactly what auth_state observations should look like (ADR-0017 sec 3).
    The validator MUST let them through."""
    from praxis.adapters import assert_auth_state_observation_safe

    accepted_values = (
        "a session cookie is set after a successful login",
        "GET /api/user returns 200 with the just-issued session cookie",
        "the response body has a user.username field, indicating an authenticated session",
        "navigating to /editor renders the article editor (logged-in affordance)",
    )
    for value in accepted_values:
        obs = ObservedSignal(
            kind="success", type="network",
            value=value, present=True,
            source_type="agent", source_id="conduit-1",
        )
        # Should NOT raise.
        assert_auth_state_observation_safe(obs)


def test_assert_auth_state_observation_safe_inspects_raw_value_pre_redaction() -> None:
    """The boundary check inspects the raw observation value the adapter saw
    BEFORE `redact()` rewrote it. If the adapter scrubbed `Bearer xyz` to
    `<token>` but the writer still intended to persist a credential, the
    validator catches it through `raw_value`."""
    from praxis.adapters import AuthStateLeakError, assert_auth_state_observation_safe
    import pytest

    redacted = ObservedSignal(
        kind="success", type="network",
        value="Authorization: <token>",  # scrubbed by redact()
        present=True, source_type="agent", source_id="conduit-1",
    )
    raw = "Authorization: Bearer eyJabc.def.ghi"
    with pytest.raises(AuthStateLeakError):
        assert_auth_state_observation_safe(redacted, raw_value=raw)


def test_redaction_pipeline_strips_token_before_event_lands_in_store(tmp_path) -> None:
    """End-to-end: an adapter writing an observation carrying a raw bearer
    token never lets that token reach the append-only store. ADR-0017 sec 3:
    the adapter is the redaction point; the store sees only redacted values."""
    from praxis.adapters import BrowserUseAdapter
    from praxis.store import FileEventStore

    store = FileEventStore(tmp_path)
    adapter = BrowserUseAdapter(
        store, target=Target(app="conduit"),
        seeds={"login": _seed()}, current_version="1",
    )
    leaky = ObservedSignal(
        kind="success", type="network",
        value="GET /api/user returns 200 with Authorization: Bearer eyJabc.def.ghi",
        present=True, source_type="agent", source_id="conduit-1",
    )
    adapter.write_observations("login", "conduit-1", [leaky], observed_app_version="1")
    stored = store.read("login")[0]
    persisted_value = stored.signals[0].value
    assert "eyJabc" not in persisted_value
    assert "def.ghi" not in persisted_value
    # The semantic frame survived ("GET /api/user returns 200").
    assert "/api/user" in persisted_value
