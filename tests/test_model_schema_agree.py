"""The JSON Schema is the single source of truth for shape (ADR-0002); the pydantic
model mirrors it. This test asserts they agree on the load-bearing parts, so the two
cannot silently drift."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schema" / "knowledge.schema.json").read_text())


def test_top_level_required_fields_match() -> None:
    from praxis.model import KnowledgeFile

    schema_required = set(SCHEMA["required"])
    model_required = {
        name for name, f in KnowledgeFile.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_signal_required_fields_match() -> None:
    from praxis.model import Signal

    schema_required = set(SCHEMA["$defs"]["signal"]["required"])
    model_required = {
        name for name, f in Signal.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_provenance_required_fields_match() -> None:
    from praxis.model import Provenance

    schema_required = set(SCHEMA["$defs"]["provenance"]["required"])
    model_required = {
        name for name, f in Provenance.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_enums_match() -> None:
    from praxis.model import SignalType, SourceType, Status

    assert [t.value for t in SignalType] == SCHEMA["$defs"]["signal"]["properties"]["type"]["enum"]
    assert ([s.value for s in SourceType]
            == SCHEMA["$defs"]["provenance"]["properties"]["source_type"]["enum"])
    assert [s.value for s in Status] == SCHEMA["$defs"]["status"]["enum"]


def test_signal_type_order_is_most_to_least_durable() -> None:
    # The oracle relies on this ordering being meaningful (different types are
    # independent evidence); freeze it so a reorder is a conscious change.
    from praxis.model import SignalType

    assert [t.value for t in SignalType] == [
        "behavioral", "network", "accessibility", "text", "url", "visual",
    ]


def test_model_rejects_signal_without_provenance() -> None:
    import pytest
    from pydantic import ValidationError

    from praxis.model import Signal

    with pytest.raises(ValidationError):
        Signal.model_validate({"type": "behavioral", "value": "x",
                               "confidence": 0.9, "status": "believed"})


# --- ADR-0030: value_predicate is optional and validated at the write boundary -


def _signal_with_predicate(predicate: str | None) -> dict:
    d = {
        "type": "url",
        "value": "the editor route for the just-created campaign",
        "provenance": {"source_type": "human", "source_id": "pablo-seed",
                       "last_verified": "2026-06-08T00:00:00Z",
                       "observation_count": 1},
        "confidence": 1.0, "status": "believed",
    }
    if predicate is not None:
        d["value_predicate"] = predicate
    return d


def test_value_predicate_is_optional_and_signal_required_set_unchanged() -> None:
    """`value_predicate` is additive: the signal required set stays exactly the
    Phase-1 set, so every existing free-text seed validates unchanged."""
    from praxis.model import Signal

    schema_required = set(SCHEMA["$defs"]["signal"]["required"])
    model_required = {
        name for name, f in Signal.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required
    assert "value_predicate" not in schema_required
    assert Signal.model_fields["value_predicate"].is_required() is False
    # A free-text signal (no predicate) validates and leaves the field None.
    s = Signal.model_validate(_signal_with_predicate(None))
    assert s.value_predicate is None


def test_valid_value_predicate_passes_write_boundary() -> None:
    from praxis.model import Signal

    s = Signal.model_validate(
        _signal_with_predicate("the route matches /Box/Editor/{seg:numeric}")
    )
    assert s.value_predicate == "the route matches /Box/Editor/{seg:numeric}"


def test_malformed_value_predicate_is_a_loud_rejection() -> None:
    """A malformed predicate fails LOUDLY at the write boundary (ADR-0030
    decision 6), never a silent downgrade to free-text."""
    import pytest
    from pydantic import ValidationError

    from praxis.model import Signal

    # no-invariant (one slot), unknown shape, unbalanced braces, stopword-only.
    for bad in ("{anything}", "route /Box/Editor/{seg:semver}",
                "id equals {campaign_id", "the {x}"):
        with pytest.raises(ValidationError) as ei:
            Signal.model_validate(_signal_with_predicate(bad))
        assert "value_predicate rejected" in str(ei.value)


# --- ADR-0031: check is optional, typed, and validated at the write boundary --


def _signal_with_check(check: dict | None) -> dict:
    d = {
        "type": "network",
        "value": "a fresh list load returns one fewer campaign",
        "provenance": {"source_type": "human", "source_id": "pablo-seed",
                       "last_verified": "2026-06-09T00:00:00Z",
                       "observation_count": 1},
        "confidence": 1.0, "status": "believed",
    }
    if check is not None:
        d["check"] = check
    return d


def test_check_is_optional_and_signal_required_set_unchanged() -> None:
    """`check` is additive: the signal required set stays the Phase-1 set, so
    every existing seed validates unchanged (ADR-0031 decision 1)."""
    from praxis.model import Signal

    schema_required = set(SCHEMA["$defs"]["signal"]["required"])
    model_required = {
        name for name, f in Signal.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required
    assert "check" not in schema_required
    assert Signal.model_fields["check"].is_required() is False
    s = Signal.model_validate(_signal_with_check(None))
    assert s.check is None


def test_check_property_registered_in_schema() -> None:
    assert "check" in SCHEMA["$defs"]["signal"]["properties"]
    assert "check" in SCHEMA["$defs"]
    assert "list_count_delta_check" in SCHEMA["$defs"]
    assert "element_membership_check" in SCHEMA["$defs"]


def test_valid_checks_pass_write_boundary() -> None:
    from praxis.model import ElementMembershipCheck, ListCountDeltaCheck, Signal

    a = Signal.model_validate(
        _signal_with_check({"kind": "list_count_delta", "expect_delta": -1})
    )
    assert isinstance(a.check, ListCountDeltaCheck)
    b = Signal.model_validate(_signal_with_check(
        {"kind": "element_membership", "identifier_slot": "campaign_id",
         "expect": "absent"}
    ))
    assert isinstance(b.check, ElementMembershipCheck)


def test_malformed_check_is_a_loud_rejection() -> None:
    import pytest
    from pydantic import ValidationError

    from praxis.model import Signal

    bad_checks = (
        {"kind": "http_status", "code": 200},               # unknown kind
        {"kind": "list_count_delta", "expect_delta": "x"},   # non-int delta
        {"kind": "element_membership", "identifier_slot": "",
         "expect": "absent"},                                # empty slot
        {"kind": "element_membership", "identifier_slot": "id",
         "expect": "gone"},                                  # bad expect
    )
    for bad in bad_checks:
        with pytest.raises(ValidationError):
            Signal.model_validate(_signal_with_check(bad))


def test_knowledge_file_with_check_round_trips_through_yaml_and_schema() -> None:
    from praxis.model import (
        KnowledgeFile,
        dumps,
        loads,
        validate_against_json_schema,
    )

    src = {
        "schema_version": "0",
        "goal_id": "delete-a-campaign",
        "goal": "a user can archive a campaign",
        "target": {"app": "digioh"},
        "success_signals": [{
            "type": "network",
            "value": "a fresh list load returns one fewer campaign",
            "check": {"kind": "list_count_delta", "expect_delta": -1},
            "provenance": {"source_type": "human", "source_id": "pablo-seed",
                           "last_verified": "2026-06-09T00:00:00Z",
                           "observation_count": 1},
            "confidence": 1.0, "status": "believed",
        }],
        "meta": {"created_at": "2026-06-09T00:00:00Z",
                 "updated_at": "2026-06-09T00:00:00Z"},
    }
    kf = KnowledgeFile.model_validate(src)
    assert kf.success_signals[0].check is not None
    kf2 = loads(dumps(kf))
    assert kf2.success_signals[0].check == kf.success_signals[0].check
    validate_against_json_schema(kf.model_dump(mode="json", exclude_none=True))


# --- Phase-1 schema activation: risks + uncertainties + triggers -------------


def test_risk_required_fields_match() -> None:
    from praxis.model import Risk

    schema_required = set(SCHEMA["$defs"]["risk"]["required"])
    model_required = {
        name for name, f in Risk.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_uncertainty_required_fields_match() -> None:
    from praxis.model import Uncertainty

    schema_required = set(SCHEMA["$defs"]["uncertainty"]["required"])
    model_required = {
        name for name, f in Uncertainty.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_trigger_discriminator_dispatches_http_vs_sequence() -> None:
    from praxis.model import HttpTrigger, Risk, SequenceTrigger

    base_provenance = {
        "source_type": "human", "source_id": "AC-RISK-1",
        "last_verified": "2026-06-07T00:00:00Z", "observation_count": 1,
    }
    http = Risk.model_validate({
        "id": "r1", "description": "x",
        "trigger": {"kind": "http", "method": "POST", "path": "/x", "expect": "y"},
        "provenance": base_provenance, "confidence": 1.0, "status": "believed",
    })
    seq = Risk.model_validate({
        "id": "r2", "description": "x",
        "trigger": {"kind": "sequence", "n": 2, "action": "z", "expect": "y"},
        "provenance": base_provenance, "confidence": 1.0, "status": "believed",
    })
    assert isinstance(http.trigger, HttpTrigger)
    assert isinstance(seq.trigger, SequenceTrigger)


def test_trigger_rejects_free_text_and_invalid_method() -> None:
    import pytest
    from pydantic import ValidationError

    from praxis.model import Risk

    base_provenance = {
        "source_type": "human", "source_id": "x",
        "last_verified": "2026-06-07T00:00:00Z", "observation_count": 1,
    }
    # Plain-string trigger is rejected (no `kind` discriminator).
    with pytest.raises(ValidationError):
        Risk.model_validate({
            "id": "r", "description": "x",
            "trigger": "under high load",
            "provenance": base_provenance, "confidence": 1.0, "status": "believed",
        })
    # HTTP-shaped trigger with an unsupported method is rejected.
    with pytest.raises(ValidationError):
        Risk.model_validate({
            "id": "r", "description": "x",
            "trigger": {"kind": "http", "method": "FETCH", "path": "/x", "expect": "y"},
            "provenance": base_provenance, "confidence": 1.0, "status": "believed",
        })


def test_phase_0_files_still_validate_without_risks_or_uncertainties() -> None:
    """Schema additions are additive: a Phase-0 file lacking risks/uncertainties
    must still validate against the active Phase-1 schema (ADR-0009)."""
    from praxis.model import KnowledgeFile

    minimal = {
        "schema_version": "0",
        "goal_id": "g",
        "goal": "a user can do X",
        "target": {"app": "acme"},
        "success_signals": [{
            "type": "behavioral", "value": "v",
            "provenance": {"source_type": "agent", "source_id": "a1",
                            "last_verified": "2026-06-07T00:00:00Z",
                            "observation_count": 1},
            "confidence": 0.5, "status": "contested",
        }],
        "meta": {"created_at": "2026-06-07T00:00:00Z",
                 "updated_at": "2026-06-07T00:00:00Z"},
    }
    kf = KnowledgeFile.model_validate(minimal)
    assert kf.risks is None
    assert kf.uncertainties is None
    # Phase-2 additive: auth_state defaults to None (ADR-0017 sec 1).
    assert kf.auth_state is None


# --- Phase-2 schema activation: auth_state additive projected field (ADR-0017) ---


def test_auth_state_required_fields_match() -> None:
    """The model and schema must agree on the auth_state required-field set
    (ADR-0017 sec 4: same-commit update of schema + model + agreement test)."""
    from praxis.model import AuthState

    schema_required = set(SCHEMA["$defs"]["auth_state"]["required"])
    model_required = {
        name for name, f in AuthState.model_fields.items() if f.is_required()
    }
    assert schema_required == model_required


def test_auth_state_top_level_property_present_in_schema() -> None:
    """`auth_state` is registered as a top-level optional property; both the
    schema and the model agree it is additive (ADR-0017 sec 1)."""
    from praxis.model import KnowledgeFile

    assert "auth_state" in SCHEMA["properties"]
    assert "auth_state" in KnowledgeFile.model_fields
    # Optional: not in schema.required, not in model.required.
    assert "auth_state" not in SCHEMA["required"]
    assert KnowledgeFile.model_fields["auth_state"].is_required() is False


def test_auth_state_accepts_canonical_roles() -> None:
    from praxis.model import AuthState

    for scope in ("anonymous", "user", "admin"):
        # 'anonymous' with authenticated=False is the unauthenticated case;
        # use authenticated=True for 'user'/'admin'.
        if scope == "anonymous":
            a = AuthState(authenticated=False, scope=None)
            assert a.scope is None
        else:
            a = AuthState(authenticated=True, scope=scope)
            assert a.scope == scope


def test_auth_state_accepts_sut_specific_role() -> None:
    """A SUT registers its own role string (e.g. Conduit `author`) by using
    it in seeded knowledge; the validator only rejects forbidden tokens
    (ADR-0017 sec 1)."""
    from praxis.model import AuthState

    a = AuthState(authenticated=True, scope="author")
    assert a.scope == "author"


def test_auth_state_null_scope_allowed_when_authenticated_but_thin() -> None:
    """`scope=None` is permitted when evidence is too thin to claim a role
    (ADR-0017 sec 1)."""
    from praxis.model import AuthState

    a = AuthState(authenticated=True, scope=None)
    assert a.scope is None


def test_auth_state_rejects_scope_with_forbidden_tokens() -> None:
    """ADR-0017 sec 2: scope must never carry tokens, cookies, user/session
    ids, or PII. The validator is loud (pydantic ValidationError) so wrong
    writes are caught at the boundary."""
    import pytest
    from pydantic import ValidationError

    from praxis.model import AuthState

    forbidden_scopes = (
        "bearer abc123",
        "Cookie=session=xyz",
        "user_id=42",
        "session_id=deadbeef",
        "sid:abc",
        "uid 17",
        "jwt eyJhbGciOiJIUzI1NiJ9",
        "api_key=foo",
        "apikey=bar",
        "alice@example.com",
        "tenant_id=acme",
        "org_id=mindcloud",
        "workspace_id=42",
    )
    for scope in forbidden_scopes:
        with pytest.raises(ValidationError):
            AuthState(authenticated=True, scope=scope)


def test_auth_state_rejects_raw_jwt_shape_in_scope() -> None:
    """Even a scope value that looks like a bare JWT (no leading 'jwt' token)
    is rejected by shape (ADR-0017 sec 2)."""
    import pytest
    from pydantic import ValidationError

    from praxis.model import AuthState

    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMxMjMifQ.signaturePart"
    with pytest.raises(ValidationError):
        AuthState(authenticated=True, scope=jwt_like)


def test_auth_state_rejects_scope_when_unauthenticated() -> None:
    """ADR-0017 sec 1: scope MUST be null when authenticated is false. The
    model rejects 'authenticated=False, scope=user' loudly."""
    import pytest
    from pydantic import ValidationError

    from praxis.model import AuthState

    with pytest.raises(ValidationError):
        AuthState(authenticated=False, scope="user")


def test_auth_state_rejects_empty_or_whitespace_scope() -> None:
    import pytest
    from pydantic import ValidationError

    from praxis.model import AuthState

    for bad in ("", "   "):
        with pytest.raises(ValidationError):
            AuthState(authenticated=True, scope=bad)


def test_knowledge_file_with_auth_state_round_trips_through_yaml() -> None:
    """A KnowledgeFile carrying auth_state survives YAML round-trip and
    validates against the JSON Schema (ADR-0017 sec 4)."""
    from praxis.model import KnowledgeFile, dumps, loads, validate_against_json_schema

    src = {
        "schema_version": "0",
        "goal_id": "publish_article",
        "goal": "an authenticated user can publish an article",
        "target": {"app": "conduit"},
        "success_signals": [{
            "type": "network", "value": "POST /api/articles returns 201",
            "provenance": {"source_type": "human", "source_id": "pablo-seed",
                           "last_verified": "2026-06-07T00:00:00Z",
                           "observation_count": 1},
            "confidence": 1.0, "status": "believed",
        }],
        "auth_state": {"authenticated": True, "scope": "user"},
        "meta": {"created_at": "2026-06-07T00:00:00Z",
                 "updated_at": "2026-06-07T00:00:00Z"},
    }
    kf = KnowledgeFile.model_validate(src)
    assert kf.auth_state is not None
    assert kf.auth_state.scope == "user"
    text = dumps(kf)
    kf2 = loads(text)
    assert kf2.auth_state == kf.auth_state
    # Same dict round-trips against the JSON Schema (no extra/missing keys).
    validate_against_json_schema(kf.model_dump(mode="json", exclude_none=True))


# --- ADR-0027: auth_state.being_tested additive declaration field ---


def test_auth_state_being_tested_optional_and_defaults_false() -> None:
    """`being_tested` is an OPTIONAL boolean defaulting to false in both the
    schema and the model (ADR-0027 decision 1), so existing knowledge files
    with no `being_tested` key still validate as precondition goals."""
    from praxis.model import AuthState

    props = SCHEMA["$defs"]["auth_state"]["properties"]
    assert "being_tested" in props
    assert props["being_tested"]["type"] == "boolean"
    assert props["being_tested"]["default"] is False
    # Not required in the schema, not required in the model.
    assert "being_tested" not in SCHEMA["$defs"]["auth_state"]["required"]
    assert AuthState.model_fields["being_tested"].is_required() is False
    # Absent key -> precondition (false).
    a = AuthState(authenticated=True, scope="user")
    assert a.being_tested is False


def test_auth_state_being_tested_true_marks_subject_and_round_trips() -> None:
    """A goal that declares authentication the subject under test
    (`being_tested: true`) round-trips through YAML and the JSON Schema."""
    from praxis.model import KnowledgeFile, dumps, loads, validate_against_json_schema

    src = {
        "schema_version": "0",
        "goal_id": "login_reach_dashboard",
        "goal": "a user can log in and reach the dashboard",
        "target": {"app": "digioh"},
        "success_signals": [{
            "type": "behavioral", "value": "dashboard renders after login",
            "provenance": {"source_type": "human", "source_id": "pablo-seed",
                           "last_verified": "2026-06-08T00:00:00Z",
                           "observation_count": 1},
            "confidence": 1.0, "status": "believed",
        }],
        "auth_state": {"authenticated": True, "scope": "user",
                       "being_tested": True},
        "meta": {"created_at": "2026-06-08T00:00:00Z",
                 "updated_at": "2026-06-08T00:00:00Z"},
    }
    kf = KnowledgeFile.model_validate(src)
    assert kf.auth_state is not None
    assert kf.auth_state.being_tested is True
    kf2 = loads(dumps(kf))
    assert kf2.auth_state == kf.auth_state
    validate_against_json_schema(kf.model_dump(mode="json", exclude_none=True))


def test_schema_rejects_extra_keys_inside_auth_state() -> None:
    """ADR-0017 sec 5: extending auth_state with tenant_id / org_id /
    workspace_id / token / cookie etc is forbidden. The schema's
    `additionalProperties: false` rejects them, the model mirrors it."""
    import pytest
    from pydantic import ValidationError

    from praxis.model import AuthState, validate_against_json_schema

    # Schema-level rejection.
    import jsonschema

    bad = {"authenticated": True, "scope": "user", "tenant_id": "acme"}
    with pytest.raises(jsonschema.ValidationError):
        # validate just the sub-object as part of a complete KnowledgeFile;
        # easier to assert directly on AuthState since the schema enforces
        # `additionalProperties: false` on the auth_state subtree.
        validate_against_json_schema({
            "schema_version": "0", "goal_id": "g", "goal": "x",
            "target": {"app": "a"},
            "success_signals": [{
                "type": "behavioral", "value": "v",
                "provenance": {"source_type": "agent", "source_id": "x",
                               "last_verified": "2026-06-07T00:00:00Z",
                               "observation_count": 1},
                "confidence": 0.5, "status": "contested",
            }],
            "auth_state": bad,
            "meta": {"created_at": "2026-06-07T00:00:00Z",
                     "updated_at": "2026-06-07T00:00:00Z"},
        })

    # Model-level rejection (extra='forbid' via _Base).
    with pytest.raises(ValidationError):
        AuthState.model_validate(bad)
