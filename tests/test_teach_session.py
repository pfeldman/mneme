"""The teach session seams: the LIBRARY half of `/praxis:teach` (ADR-0022).

These tests pin the contract the `/praxis:teach` skill (Step 10) drives, all
without a browser and without an LLM (Wave 2 Step 9):

- a CONFIRMED session emits a `source_type=human` seed with provenance +
  confidence that validates against the active schema (decisions 3, 4);
- a RE-TEACH of a believed goal produces a CONTESTED candidate refinement under
  `.praxis/candidates/`, never an in-place mutation of the committed seed
  (decision 6);
- a NON-CONVERGED session (backstop tripped or the dual end condition unmet)
  writes NO goal and emits a loud, traceable not-converged event naming what was
  reached and what was missing (decision 3);
- a credential is NEVER persisted into any emitted assertion; the
  adapter-boundary validator rejects tokens / cookies / ids / PII (decision 5);
- a navigation-hint reply is stored as the behavioral / network / accessibility
  / text / url invariant it points at, NEVER a raw selector (decision 2);
- only the abstract ADR-0017 `auth_state` (authenticated + scope) is recorded.

Coverage map (the handoff's verification list):
  confirmed session emits human seed + validates ..... test_confirmed_session_*
  re-teach of believed goal -> contested candidate ... test_reteach_*
  non-converged session writes no goal + loud event .. test_not_converged_*
  no credential ever persisted ....................... test_credential_*
  navigation-hint stored as invariant not selector ... test_navigation_hint_*
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from praxis.model import (
    AuthState,
    HttpTrigger,
    Provenance,
    Risk,
    SequenceTrigger,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
    Uncertainty,
    to_jsonable,
    validate_against_json_schema,
)
from praxis.teach import (
    INVARIANT_PREFERENCE_ORDER,
    ConfirmationPrompt,
    CredentialLeak,
    CredentialPrompt,
    EndCondition,
    NavigationHintPrompt,
    NotConvergedEvent,
    PromptType,
    RolePrompt,
    SelectorLikeReply,
    TeachBudget,
    TeachSession,
    assert_no_credential_leak,
    record_navigation_hint,
)

HUMAN = "pablo"
APP_VERSION = "2026.6.8"


# --- fixtures --------------------------------------------------------------


def _session(tmp_path: Path, *, budget: TeachBudget | None = None, **kw) -> TeachSession:
    """A teach session over a fresh tmp `.praxis/` knowledge + candidates tree."""
    knowledge_dir = tmp_path / ".praxis" / "knowledge"
    candidates_dir = tmp_path / ".praxis" / "candidates"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    candidates_dir.mkdir(parents=True, exist_ok=True)
    return TeachSession(
        knowledge_dir=knowledge_dir,
        candidates_dir=candidates_dir,
        target=Target(app="testapp", environment="local"),
        confirming_human=HUMAN,
        observed_app_version=APP_VERSION,
        budget=budget or TeachBudget(),
        **kw,
    )


def _human_signal(
    value: str,
    *,
    type_: SignalType = SignalType.BEHAVIORAL,
    source_id: str = HUMAN,
    confidence: float = 1.0,
) -> Signal:
    return Signal(
        type=type_,
        value=value,
        provenance=Provenance(
            source_type=SourceType.HUMAN,
            source_id=source_id,
            observed_app_version=APP_VERSION,
            last_verified=datetime(2026, 6, 8, tzinfo=timezone.utc),
            observation_count=1,
        ),
        confidence=confidence,
        status=Status.BELIEVED,
    )


def _http_risk(risk_id: str = "open-redirect") -> Risk:
    return Risk(
        id=risk_id,
        description="login callback redirects to an attacker-controlled host",
        trigger=HttpTrigger(
            method="GET",
            path="/login/callback",
            expect="Location header matches the configured origin",
        ),
        provenance=Provenance(
            source_type=SourceType.HUMAN,
            source_id=HUMAN,
            last_verified=datetime(2026, 6, 8, tzinfo=timezone.utc),
            observation_count=1,
        ),
        confidence=0.9,
        status=Status.BELIEVED,
    )


def _seed_believed_goal(tmp_path: Path, goal_id: str) -> Path:
    """Write a believed seed file under `.praxis/knowledge/` so a re-teach must
    route into a contested candidate (decision 6)."""
    from praxis.model import KnowledgeFile, Meta, dump

    knowledge_dir = tmp_path / ".praxis" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    kf = KnowledgeFile(
        schema_version="0",
        goal_id=goal_id,
        goal=f"goal {goal_id}",
        target=Target(app="testapp", environment="local"),
        success_signals=[_human_signal("an authenticated home state is reachable")],
        meta=Meta(
            created_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
            updated_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
        ),
    )
    out = knowledge_dir / f"{goal_id}.knowledge.yaml"
    dump(kf, out)
    return out


# --- the typed prompt protocol is data (decision 2) ------------------------


def test_typed_prompt_protocol_has_exactly_four_types() -> None:
    """The four declared question types are modeled as data so the skill and the
    tests speak the same shapes (ADR-0022 decision 2)."""
    assert {t.value for t in PromptType} == {
        "credential", "navigation_hint", "role", "confirmation",
    }
    assert CredentialPrompt.for_key("APP_PASSWORD").prompt_type is PromptType.CREDENTIAL
    assert NavigationHintPrompt.asking("where is the editor?").prompt_type \
        is PromptType.NAVIGATION_HINT
    assert RolePrompt.asking().prompt_type is PromptType.ROLE
    assert ConfirmationPrompt.asking("is this the success state?").prompt_type \
        is PromptType.CONFIRMATION


def test_credential_prompt_carries_only_the_key_name_never_a_value() -> None:
    """A credential prompt carries the KEY NAME the secrets channel supplies,
    never the secret value: the value is the human's reply (decision 5)."""
    p = CredentialPrompt.for_key("APP_PASSWORD")
    assert p.key == "APP_PASSWORD"
    # No field on the prompt holds a value; only the key name and question text.
    assert "APP_PASSWORD" in p.question
    assert not hasattr(p, "value")


# --- navigation hints are invariants, never selectors (decision 2) ---------


def test_navigation_hint_recorded_as_behavioral_invariant() -> None:
    """A navigation-hint reply is stored as the behavior it points at, in the
    durable invariant order (behavioral first), never a click target."""
    inv = record_navigation_hint("the editor opens in a modal after clicking New")
    assert inv.type is SignalType.BEHAVIORAL
    assert "editor opens" in inv.value
    # The preference order is the ADR five-non-negotiables hierarchy.
    assert INVARIANT_PREFERENCE_ORDER[0] is SignalType.BEHAVIORAL
    assert SignalType.VISUAL not in INVARIANT_PREFERENCE_ORDER


def test_navigation_hint_can_record_a_network_invariant() -> None:
    """A hint that points at a network fact is recorded as a network invariant."""
    inv = record_navigation_hint(
        "a POST to the publish endpoint returns 2xx", invariant_type=SignalType.NETWORK,
    )
    assert inv.type is SignalType.NETWORK


@pytest.mark.parametrize(
    "selector_reply",
    [
        "css=.publish-button",
        "//button[@id='publish']",
        "#publish-btn",
        ".btn-primary",
        "document.querySelector('.modal')",
        "[data-testid='editor']",
    ],
)
def test_navigation_hint_rejects_selector_shaped_replies(selector_reply: str) -> None:
    """A reply naming a raw CSS selector / XPath / coordinate is rejected; a
    selector is never durable knowledge (AGENTS.md non-negotiable 1)."""
    with pytest.raises(SelectorLikeReply):
        record_navigation_hint(selector_reply)


def test_navigation_hint_refuses_visual_or_coordinate_type() -> None:
    """A hint cannot be downgraded to a `visual` signal: that is a coordinate in
    disguise (decision 2)."""
    with pytest.raises(ValueError):
        record_navigation_hint("the top-right button", invariant_type=SignalType.VISUAL)


# --- a confirmed session emits a human seed that validates (decisions 3, 4) -


def test_confirmed_session_emits_human_seed_validating_against_schema(
    tmp_path: Path,
) -> None:
    """A confirmed session (happy-path-observed AND human-confirm) emits a goal
    YAML whose success oracle is `source_type=human` (the confirming human),
    with provenance + confidence on every signal and risk; the emitted file
    validates against the active JSON Schema (decisions 3, 4; ADR-0004)."""
    s = _session(tmp_path)
    seed = s.build_seed(
        goal_id="login",
        goal="a returning user can establish an authenticated session",
        success_signals=[
            _human_signal("a Sign out control becomes available"),
            _human_signal(
                "a POST to the session endpoint returns 2xx",
                type_=SignalType.NETWORK,
            ),
        ],
        failure_signals=[
            _human_signal(
                "an inline invalid-credentials error appears and no session is set",
                type_=SignalType.TEXT,
            ),
        ],
        risks=[_http_risk()],
        uncertainties=[
            Uncertainty(
                id="u-session-scope",
                question="is the session cookie scoped per tab or per browser?",
                raised_by=HUMAN,
                raised_at=datetime(2026, 6, 8, tzinfo=timezone.utc),
            ),
        ],
        auth_state=AuthState(authenticated=True, scope="user"),
    )

    end = EndCondition(happy_path_observed=True, human_confirmed=True)
    outcome = s.finish(
        goal_id="login",
        goal="a returning user can establish an authenticated session",
        end_condition=end,
        actions=4,
        seed=seed,
    )

    assert outcome.converged
    assert not outcome.contested_refinement
    assert outcome.knowledge_path is not None and outcome.knowledge_path.exists()

    # The success oracle is a human seed (ADR-0005 first-oracle path).
    assert outcome.knowledge is not None
    for sig in outcome.knowledge.success_signals:
        assert sig.provenance.source_type is SourceType.HUMAN
        assert sig.provenance.source_id == HUMAN
        assert 0.0 <= sig.confidence <= 1.0

    # The emitted YAML validates against the active schema (AGENTS.md DoD).
    written = yaml.safe_load(outcome.knowledge_path.read_text(encoding="utf-8"))
    validate_against_json_schema(written)
    # And the in-memory model round-trips through the schema too.
    validate_against_json_schema(to_jsonable(outcome.knowledge))


def test_confirmed_session_records_only_abstract_auth_state(tmp_path: Path) -> None:
    """Only the ADR-0017 abstract auth_state (authenticated + scope) is recorded;
    the credential that produced it is never an emitted field (decision 5)."""
    s = _session(tmp_path)
    seed = s.build_seed(
        goal_id="admin",
        goal="an admin can reach the admin console",
        success_signals=[_human_signal("the admin console is reachable")],
        auth_state=AuthState(authenticated=True, scope="admin"),
    )
    assert seed.auth_state is not None
    assert seed.auth_state.authenticated is True
    assert seed.auth_state.scope == "admin"
    # The model already rejects a token-shaped scope; confirm the contract holds.
    with pytest.raises(Exception):
        AuthState(authenticated=True, scope="Bearer abc.def.ghijklmn")


def test_build_seed_rejects_an_agent_sourced_oracle(tmp_path: Path) -> None:
    """A teach session never self-certifies: a success signal sourced from an
    agent (not the confirming human) is rejected (ADR-0005, decision 4)."""
    s = _session(tmp_path)
    agent_sig = Signal(
        type=SignalType.BEHAVIORAL,
        value="a Sign out control becomes available",
        provenance=Provenance(
            source_type=SourceType.AGENT,
            source_id="explorer-1",
            last_verified=datetime(2026, 6, 8, tzinfo=timezone.utc),
            observation_count=9,
        ),
        confidence=0.95,
        status=Status.BELIEVED,
    )
    with pytest.raises(ValueError, match="human-seeded"):
        s.build_seed(
            goal_id="login", goal="login",
            success_signals=[agent_sig],
        )


def test_build_seed_rejects_a_source_id_that_is_not_the_confirming_human(
    tmp_path: Path,
) -> None:
    """The human who CONFIRMED the success state must author the seed: a human
    signal with a different source_id is rejected (decision 4)."""
    s = _session(tmp_path)
    other = _human_signal("the home state is reachable", source_id="someone-else")
    with pytest.raises(ValueError, match="confirming human"):
        s.build_seed(goal_id="login", goal="login", success_signals=[other])


# --- a re-teach of a believed goal produces a contested candidate (decision 6)


def test_reteach_of_believed_goal_appends_contested_candidate_not_a_mutation(
    tmp_path: Path,
) -> None:
    """A re-teach of a goal that already exists believed does NOT overwrite the
    committed seed: it appends a contested candidate refinement under
    `.praxis/candidates/` (ADR-0014, decision 6). The believed seed is preserved
    byte-for-byte; promotion stays a human seed via git merge."""
    seed_path = _seed_believed_goal(tmp_path, "login")
    before = seed_path.read_text(encoding="utf-8")

    s = _session(tmp_path)
    assert s.goal_already_believed("login") is True

    end = EndCondition(happy_path_observed=True, human_confirmed=True)
    outcome = s.finish(
        goal_id="login",
        goal="goal login",
        end_condition=end,
        actions=3,
        refinement_risks=[_http_risk("open-redirect-v2")],
    )

    # It converged into a contested refinement, NOT a knowledge write.
    assert outcome.converged
    assert outcome.contested_refinement
    assert outcome.knowledge is None and outcome.knowledge_path is None
    assert len(outcome.candidate_paths) == 1

    # The committed seed was NOT mutated in place (append-only, ADR-0001).
    assert seed_path.read_text(encoding="utf-8") == before

    # The candidate landed under candidates/login/ as a contested CandidateEvent.
    cand_dir = tmp_path / ".praxis" / "candidates" / "login"
    files = sorted(cand_dir.glob("*.yaml"))
    assert len(files) == 1
    payload = yaml.safe_load(files[0].read_text(encoding="utf-8"))
    assert payload["goal_id"] == "login"
    assert payload["payload"]["kind"] == "candidate_risk"
    assert payload["payload"]["risk"]["status"] == "contested"
    # The candidate file is named by its observation event id, not the risk id.
    assert files[0].stem != "open-redirect-v2"


def test_goal_not_believed_when_no_seed_file_exists(tmp_path: Path) -> None:
    """A brand-new goal has no believed seed: the session emits a fresh human
    seed rather than a contested candidate (decision 6 only fires for believed)."""
    s = _session(tmp_path)
    assert s.goal_already_believed("brand-new") is False


def test_reteach_refinement_with_an_uncertainty_only(tmp_path: Path) -> None:
    """A re-teach may propose an uncertainty refinement (a question), also as a
    contested candidate, never an edit (decision 6)."""
    _seed_believed_goal(tmp_path, "checkout")
    s = _session(tmp_path)
    end = EndCondition(happy_path_observed=True, human_confirmed=True)
    outcome = s.finish(
        goal_id="checkout",
        goal="goal checkout",
        end_condition=end,
        actions=2,
        refinement_uncertainties=[
            Uncertainty(
                id="u-double-charge",
                question="does a double submit charge the card twice?",
                raised_by=HUMAN,
                raised_at=datetime(2026, 6, 8, tzinfo=timezone.utc),
            ),
        ],
    )
    assert outcome.converged and outcome.contested_refinement
    cand = sorted((tmp_path / ".praxis" / "candidates" / "checkout").glob("*.yaml"))
    assert len(cand) == 1
    payload = yaml.safe_load(cand[0].read_text(encoding="utf-8"))
    assert payload["payload"]["kind"] == "candidate_uncertainty"


# --- the dual end condition (decision 3) -----------------------------------


def test_end_condition_requires_both_halves() -> None:
    """A session ends successfully only when BOTH happy-path-observed AND
    human-confirm hold; neither half alone ends it (decision 3)."""
    assert not EndCondition(happy_path_observed=True, human_confirmed=False).met()
    assert not EndCondition(happy_path_observed=False, human_confirmed=True).met()
    assert EndCondition(happy_path_observed=True, human_confirmed=True).met()


def test_not_converged_observed_but_unconfirmed_writes_no_goal(tmp_path: Path) -> None:
    """An observed-but-unconfirmed path stays open: no goal is written and a loud
    not-converged event names human-confirm as missing (decision 3)."""
    s = _session(tmp_path)
    seed = s.build_seed(
        goal_id="login", goal="login",
        success_signals=[_human_signal("a Sign out control becomes available")],
    )
    end = EndCondition(happy_path_observed=True, human_confirmed=False)
    outcome = s.finish(
        goal_id="login", goal="login", end_condition=end, actions=2, seed=seed,
    )
    assert not outcome.converged
    assert outcome.knowledge_path is None
    assert not (tmp_path / ".praxis" / "knowledge" / "login.knowledge.yaml").exists()
    ev = outcome.not_converged
    assert isinstance(ev, NotConvergedEvent)
    assert "happy-path-observed" in ev.reached
    assert "human-confirm" in ev.missing
    # The event is traceable: it names reached + missing and serializes cleanly.
    assert "did NOT converge" in ev.message()
    assert ev.as_dict()["missing"] == ["human-confirm"]


def test_not_converged_confirmed_without_observed_path_writes_no_goal(
    tmp_path: Path,
) -> None:
    """A confirmation without an observed path is rejected: there is no signal to
    seed, so the session writes no goal (decision 3)."""
    s = _session(tmp_path)
    end = EndCondition(happy_path_observed=False, human_confirmed=True)
    outcome = s.finish(
        goal_id="login", goal="login", end_condition=end, actions=1,
    )
    assert not outcome.converged
    assert outcome.not_converged is not None
    assert "happy-path-observed" in outcome.not_converged.missing


def test_not_converged_on_action_budget_backstop(tmp_path: Path) -> None:
    """The action budget bounds a non-converging session: exceeding it writes no
    goal and emits a loud not-converged event naming the budget (decision 3)."""
    s = _session(tmp_path, budget=TeachBudget(max_actions=3))
    end = EndCondition(happy_path_observed=False, human_confirmed=False)
    outcome = s.finish(
        goal_id="hard-goal", goal="a goal that never converged",
        end_condition=end, actions=10,
    )
    assert not outcome.converged
    assert outcome.not_converged is not None
    assert "backstop exhausted" in outcome.not_converged.reason
    assert "actions 10 > budget 3" in outcome.not_converged.reason
    assert outcome.not_converged.missing == ["happy-path-observed", "human-confirm"]
    # No goal file anywhere.
    assert not list((tmp_path / ".praxis" / "knowledge").glob("*.knowledge.yaml"))


def test_not_converged_on_wall_time_backstop(tmp_path: Path) -> None:
    """The wall-clock backstop bounds a session that never converges: a deadline
    in the past makes `finish` emit a loud not-converged event (decision 3)."""
    # A fake monotonic clock whose first reading anchors the session start and
    # every later reading is well past the 1.0s wall budget.
    calls = {"n": 0}

    def fake_clock() -> float:
        calls["n"] += 1
        return 100.0 if calls["n"] == 1 else 200.0

    s = _session(
        tmp_path,
        budget=TeachBudget(max_wall_seconds=1.0),
        time_source=fake_clock,
    )
    end = EndCondition(happy_path_observed=True, human_confirmed=False)
    outcome = s.finish(
        goal_id="slow-goal", goal="a slow goal",
        end_condition=end, actions=1,
    )
    assert not outcome.converged
    assert outcome.not_converged is not None
    assert "wall" in outcome.not_converged.reason
    assert "budget" in outcome.not_converged.reason


def test_backstop_does_not_block_a_converged_session(tmp_path: Path) -> None:
    """If the dual end condition IS met, the backstop does not fire: an over-
    budget but successful session still commits the seed (decision 3)."""
    s = _session(tmp_path, budget=TeachBudget(max_actions=1))
    seed = s.build_seed(
        goal_id="login", goal="login",
        success_signals=[_human_signal("a Sign out control becomes available")],
    )
    end = EndCondition(happy_path_observed=True, human_confirmed=True)
    outcome = s.finish(
        goal_id="login", goal="login", end_condition=end, actions=99, seed=seed,
    )
    # Convergence wins over the budget: a confirmed seed is committed.
    assert outcome.converged
    assert outcome.knowledge_path is not None and outcome.knowledge_path.exists()


# --- no credential ever persisted (decision 5) -----------------------------


@pytest.mark.parametrize(
    "leaky_value",
    [
        # The credential VALUE shapes a real adapter trace would carry, exactly
        # what decision 5 must keep out of an emitted assertion.
        "the cookie session_id=abc123 is set",  # assignment
        "Authorization: Bearer abc123xyz",      # bearer literal
        "password=hunter2 is accepted",          # assignment
        "set-cookie: sid=Zm9vYmFy",              # cookie assignment
        "user_id=4815162342 in the response",    # id assignment
        "the account_id is 4815162342",          # long numeric id
        "a session token Zm9vYmFyYmF6cXV4MTIzNDU2 appears",  # opaque blob
        "user pablo@example.com is shown",       # PII email
        "eyJhbGciOiJ.IUzI1NiIsInR.5cCI6IkpXVCJ9",  # jwt shape
    ],
)
def test_credential_leak_rejected_in_an_emitted_signal(
    tmp_path: Path, leaky_value: str,
) -> None:
    """A success signal value that carries a credential / token / cookie / id /
    PII value is rejected at the emit boundary; the secret never reaches a
    committed file (decision 5). The check targets value SHAPES (assignments,
    Bearer literals, JWTs, emails, long opaque / numeric ids), not descriptive
    nouns: "sets a session cookie" passes, "session_id=abc123" does not."""
    s = _session(tmp_path)
    with pytest.raises(CredentialLeak):
        s.build_seed(
            goal_id="login", goal="login",
            success_signals=[_human_signal(leaky_value)],
        )


def test_credential_leak_rejected_in_a_risk_trigger_expect(tmp_path: Path) -> None:
    """A leak hiding in a risk trigger's `expect` predicate is also caught
    (decision 5): the whole emitted surface is scanned, not only signal values."""
    leaky_risk = Risk(
        id="leaky",
        description="a benign description",
        trigger=HttpTrigger(
            method="GET", path="/me",
            expect="response sets cookie session_id=leaked",
        ),
        provenance=Provenance(
            source_type=SourceType.HUMAN, source_id=HUMAN,
            last_verified=datetime(2026, 6, 8, tzinfo=timezone.utc),
            observation_count=1,
        ),
        confidence=0.9, status=Status.BELIEVED,
    )
    s = _session(tmp_path)
    with pytest.raises(CredentialLeak):
        s.build_seed(
            goal_id="login", goal="login",
            success_signals=[_human_signal("the home state is reachable")],
            risks=[leaky_risk],
        )


def _leaky_risk(
    *,
    risk_id: str = "leaky",
    description: str = "a benign description",
    mitigation: str | None = None,
    trigger: HttpTrigger | SequenceTrigger | None = None,
) -> Risk:
    """A risk whose specified surface carries a credential VALUE, for the
    decision-5 leak-surface coverage (body_or_params, SequenceTrigger.action,
    mitigation). Defaults are benign so each test isolates one leak surface."""
    return Risk(
        id=risk_id,
        description=description,
        trigger=trigger
        or HttpTrigger(
            method="GET", path="/me", expect="the home state is reachable",
        ),
        mitigation=mitigation,
        provenance=Provenance(
            source_type=SourceType.HUMAN, source_id=HUMAN,
            last_verified=datetime(2026, 6, 8, tzinfo=timezone.utc),
            observation_count=1,
        ),
        confidence=0.9, status=Status.BELIEVED,
    )


# The three real free-text / body trigger surfaces decision 5 must keep clean,
# beyond signal.value and HttpTrigger.expect: the POST body, a sequence action,
# and the risk mitigation. Each builds a risk leaking on exactly one surface.
def _login_body_leak_risk() -> Risk:
    """The canonical teach goal ("a user can log in"): the real password lands in
    the POST /login `body_or_params`. A credential KEY in the body is a leak even
    when the value is short and shape-clean."""
    return _leaky_risk(
        risk_id="login-body-leak",
        description="login credentials are submitted to the session endpoint",
        trigger=HttpTrigger(
            method="POST",
            path="/login",
            body_or_params={"username": "admin", "password": "hunter2-real-secret"},
            expect="a session is established and the home state is reachable",
        ),
    )


def _sequence_action_leak_risk() -> Risk:
    """A SequenceTrigger.action carrying a session id is a leak surface the
    narrower re-teach scan used to miss."""
    return _leaky_risk(
        risk_id="replay-leak",
        description="a replayed request with a stale session reaches a protected route",
        trigger=SequenceTrigger(
            n=2,
            action="replay with cookie session_id=abc123XYZleaked",
            expect="the second replay is rejected",
        ),
    )


def _mitigation_leak_risk() -> Risk:
    """A risk.mitigation carrying a Bearer token is a leak surface the narrower
    re-teach scan used to miss."""
    return _leaky_risk(
        risk_id="mitigation-leak",
        description="a stale credential is not rotated on logout",
        mitigation="rotate the leaked Bearer abc123xyztoken on logout",
    )


@pytest.mark.parametrize(
    "make_risk",
    [
        _login_body_leak_risk,
        _sequence_action_leak_risk,
        _mitigation_leak_risk,
    ],
    ids=["body_or_params", "sequence_action", "mitigation"],
)
def test_credential_leak_rejected_on_seed_path_across_all_trigger_surfaces(
    tmp_path: Path, make_risk,
) -> None:
    """The seed path (`build_seed` -> `assert_no_credential_leak`) rejects a
    credential VALUE on EVERY risk surface: the POST `body_or_params`, a
    `SequenceTrigger.action`, and `risk.mitigation`, not only `signal.value` and
    `HttpTrigger.expect` (decision 5). Without this, the canonical login body
    password rides verbatim into a committed seed under `.praxis/knowledge/`."""
    s = _session(tmp_path)
    with pytest.raises(CredentialLeak):
        s.build_seed(
            goal_id="login", goal="login",
            success_signals=[_human_signal("the home state is reachable")],
            risks=[make_risk()],
        )
    # And nothing was committed.
    assert not list((tmp_path / ".praxis" / "knowledge").glob("*.knowledge.yaml"))


@pytest.mark.parametrize(
    "make_risk",
    [
        _login_body_leak_risk,
        _sequence_action_leak_risk,
        _mitigation_leak_risk,
    ],
    ids=["body_or_params", "sequence_action", "mitigation"],
)
def test_credential_leak_rejected_on_reteach_path_across_all_trigger_surfaces(
    tmp_path: Path, make_risk,
) -> None:
    """The re-teach contested-refinement path rejects a credential VALUE on the
    SAME risk surfaces as the seed path (`body_or_params`, `SequenceTrigger.action`,
    `mitigation`): the defense is symmetric (decision 5). Previously the re-teach
    scan was narrower and a secret rejected on a new-goal seed was silently
    committed into a candidate file under `.praxis/candidates/`."""
    _seed_believed_goal(tmp_path, "login")
    s = _session(tmp_path)
    end = EndCondition(happy_path_observed=True, human_confirmed=True)
    with pytest.raises(CredentialLeak):
        s.finish(
            goal_id="login", goal="goal login",
            end_condition=end, actions=3,
            refinement_risks=[make_risk()],
        )
    # No candidate file was committed despite the converged dual end condition.
    assert not list((tmp_path / ".praxis" / "candidates").rglob("*.yaml"))


def test_login_body_password_does_not_land_in_a_committed_file(tmp_path: Path) -> None:
    """Regression pin for LEAK HOLE #1: the canonical teach goal POST /login body
    `{'username': 'admin', 'password': 'hunter2-real-secret'}` must NOT write the
    password verbatim into any committed file under `.praxis/`. The emit is
    rejected and the disk stays clean (decision 5, ADR-0017 sec 2)."""
    s = _session(tmp_path)
    with pytest.raises(CredentialLeak):
        s.build_seed(
            goal_id="login", goal="a user can log in",
            success_signals=[_human_signal("the home state is reachable")],
            risks=[_login_body_leak_risk()],
        )
    # Scan the whole .praxis tree on disk: the secret value is nowhere.
    praxis_root = tmp_path / ".praxis"
    for path in praxis_root.rglob("*"):
        if path.is_file():
            assert "hunter2-real-secret" not in path.read_text(encoding="utf-8")


def test_no_credential_appears_in_the_committed_seed_file(tmp_path: Path) -> None:
    """End-to-end: a confirmed session commits a seed whose file text contains no
    credential token, and an explicit sentinel credential never lands on disk
    (decision 5). The credential drives the browser; it is never an output."""
    sentinel = "s3cr3t-pw-must-not-leak-7f21"
    s = _session(tmp_path)
    seed = s.build_seed(
        goal_id="login", goal="login",
        success_signals=[
            _human_signal("a Sign out control becomes available"),
            _human_signal(
                "a 200 on the protected /me endpoint after auth",
                type_=SignalType.NETWORK,
            ),
        ],
        auth_state=AuthState(authenticated=True, scope="user"),
    )
    end = EndCondition(happy_path_observed=True, human_confirmed=True)
    outcome = s.finish(
        goal_id="login", goal="login", end_condition=end, actions=4, seed=seed,
    )
    assert outcome.knowledge_path is not None
    text = outcome.knowledge_path.read_text(encoding="utf-8")
    # The sentinel credential never appears, and neither do credential tokens.
    assert sentinel not in text
    for token in ("password", "session_id", "bearer", "cookie", "token"):
        assert token not in text.lower()
    # Only the abstract auth posture is recorded.
    assert "authenticated: true" in text
    assert "scope: user" in text


def test_assert_no_credential_leak_passes_a_clean_file(tmp_path: Path) -> None:
    """A clean emitted KnowledgeFile passes the boundary validator unchanged
    (the validator is a backstop, not a blanket blocker; decision 5)."""
    s = _session(tmp_path)
    seed = s.build_seed(
        goal_id="login", goal="login",
        success_signals=[_human_signal("a Sign out control becomes available")],
        risks=[_http_risk()],
    )
    # No raise: the clean seed crosses the boundary.
    assert_no_credential_leak(seed)
