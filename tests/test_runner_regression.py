"""R-mode runner tests (offline; no browser).

Cover prompt rendering, verdict logic, executor protocol, and report writers.
The runner is the contract layer; tests pin the shape so the LOCAL_RUN path
and an API-key path can plug into it without changes.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from praxis.adapters import BrowserUseAdapter
from praxis.model import (
    HttpTrigger,
    KnowledgeFile,
    Meta,
    Provenance,
    Risk,
    SequenceTrigger,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
)
from praxis.runner import (
    RegressionRunner,
    RegressionVerdict,
    verdict_from_observations,
    write_junit_xml,
    write_markdown_report,
)
from praxis.runner.prompts import (
    render_exploration_prompt,
    render_regression_prompt,
)
from praxis.store import FileEventStore, ObservedSignal


def _provenance(source_type: SourceType = SourceType.HUMAN,
                source_id: str = "spec-1") -> Provenance:
    return Provenance(
        source_type=source_type,
        source_id=source_id,
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )


def _login_kf() -> KnowledgeFile:
    return KnowledgeFile(
        schema_version="0",
        goal_id="login",
        goal="a user can authenticate",
        target=Target(app="testapp"),
        success_signals=[
            Signal(type=SignalType.BEHAVIORAL,
                   value="sign-out action becomes available",
                   provenance=_provenance(), confidence=1.0, status=Status.BELIEVED),
            Signal(type=SignalType.NETWORK,
                   value="POST /session returns 2xx and sets a session cookie",
                   provenance=_provenance(), confidence=1.0, status=Status.BELIEVED),
        ],
        failure_signals=[
            Signal(type=SignalType.TEXT,
                   value="invalid credentials banner",
                   provenance=_provenance(), confidence=1.0, status=Status.BELIEVED),
        ],
        risks=[
            Risk(id="lockout", description="brute-force lockout",
                 trigger=SequenceTrigger(n=4, action="submit wrong password",
                                          expect="account locked after 3rd"),
                 provenance=_provenance(), confidence=0.9, status=Status.BELIEVED),
            Risk(id="coupon-stack",
                 description="coupons stack when only one should apply",
                 trigger=HttpTrigger(method="POST", path="/cart/apply",
                                      body_or_params={"coupon": "SAVE10"},
                                      expect="returns 200 applied=true once"),
                 provenance=_provenance(), confidence=0.7, status=Status.CONTESTED),
        ],
        meta=Meta(created_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
                  updated_at=datetime(2026, 6, 7, tzinfo=timezone.utc)),
    )


# --- verdict logic ---------------------------------------------------------


def test_verdict_pass_when_all_believed_success_signals_observed() -> None:
    kf = _login_kf()
    obs = [
        ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                       value="sign-out action becomes available",
                       source_type=SourceType.AGENT, source_id="a1"),
        ObservedSignal(kind="success", type=SignalType.NETWORK,
                       value="POST /session returns 2xx and sets a session cookie",
                       source_type=SourceType.AGENT, source_id="a1"),
    ]
    verdict, ok, bad = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.PASS
    assert len(ok) == 2 and bad == []


def test_verdict_fail_when_failure_signal_observed() -> None:
    kf = _login_kf()
    obs = [
        ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                       value="sign-out action becomes available",
                       source_type=SourceType.AGENT, source_id="a1"),
        ObservedSignal(kind="failure", type=SignalType.TEXT,
                       value="invalid credentials banner",
                       source_type=SourceType.AGENT, source_id="a1"),
    ]
    verdict, _, bad = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.FAIL
    assert "invalid credentials banner" in bad


def test_verdict_uncertain_when_some_success_signals_missing() -> None:
    kf = _login_kf()
    obs = [
        ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                       value="sign-out action becomes available",
                       source_type=SourceType.AGENT, source_id="a1"),
    ]
    verdict, ok, bad = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.UNCERTAIN
    assert len(ok) == 1 and bad == []


def test_failure_signal_overrides_success() -> None:
    # Even when both success signals are seen, an observed failure trumps:
    # a regression must surface (docs/06 - the layer must be loud).
    kf = _login_kf()
    obs = [
        ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                       value="sign-out action becomes available",
                       source_type=SourceType.AGENT, source_id="a1"),
        ObservedSignal(kind="success", type=SignalType.NETWORK,
                       value="POST /session returns 2xx and sets a session cookie",
                       source_type=SourceType.AGENT, source_id="a1"),
        ObservedSignal(kind="failure", type=SignalType.TEXT,
                       value="invalid credentials banner",
                       source_type=SourceType.AGENT, source_id="a1"),
    ]
    verdict, _, bad = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.FAIL
    assert bad


def test_verdict_matches_paraphrase_substring() -> None:
    # Real agents paraphrase observed values; matcher tolerates substring
    # overlap so a literal-string-equality strictness does not force every
    # run to be uncertain.
    kf = _login_kf()
    obs = [
        ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                       value="sign-out becomes available",  # paraphrase
                       source_type=SourceType.AGENT, source_id="a1"),
        ObservedSignal(kind="success", type=SignalType.NETWORK,
                       value="POST /session returns 2xx",  # subset
                       source_type=SourceType.AGENT, source_id="a1"),
    ]
    verdict, _, _ = verdict_from_observations(kf, obs)
    assert verdict == RegressionVerdict.PASS


# --- prompt rendering ------------------------------------------------------


def test_regression_prompt_includes_signals_and_no_steps() -> None:
    kf = _login_kf()
    p = render_regression_prompt(kf, budget_actions=10)
    assert "GOAL (login)" in p
    assert "sign-out action becomes available" in p
    assert "invalid credentials banner" in p
    # No selectors / steps / element references leak into the prompt
    # (AGENTS.md non-negotiable 1).
    forbidden = ["css", "xpath", "click(", "selector", '"#', '#id=']
    for f in forbidden:
        assert f not in p.lower(), f"prompt leaked imperative artifact: {f!r}"


def test_regression_prompt_demands_each_signal_in_its_declared_type() -> None:
    """ADR-0028: the regress prompt must ask the agent to confirm EVERY success
    signal, one observation per signal, IN that signal's DECLARED type, with the
    grounding guardrail leading. The old "type the observation by what you
    actually checked" instruction must be GONE, because it fought the exact-type
    matcher and produced a false UNCERTAIN. The negative assertion catches a
    future regression of the wording."""
    kf = _login_kf()
    p = render_regression_prompt(kf, budget_actions=10)
    low = p.lower()
    # Confirm ALL signals, one observation per signal, each in its declared type.
    assert "confirm every success signal" in low
    assert "one\nobservation per signal" in low or "one observation per signal" in low
    assert "declared type" in low
    # Grounding guardrail present: never assert a signal just to complete the list.
    assert "grounded in evidence" in low
    assert "never assert a signal just to complete" in low
    assert "leave it unconfirmed" in low
    assert "do not fabricate" in low
    # The conflicting free-typing instruction is gone (ADR-0028 decision 1).
    assert "by what you actually checked" not in low


def test_regression_prompt_surfaces_a_structured_predicate(tmp_path: Path) -> None:
    """ADR-0030 wiring: when a signal carries a `value_predicate`, the prompt must
    show it and tell the agent to confirm IN that exact shape (fill each {slot}).
    The matcher fullmatches the observation against the predicate, so without this
    the agent emits free prose that can never match. Pins the prompt half of the
    facts feature."""
    from praxis.model import Signal, SignalType, Status

    kf = _login_kf()
    kf.success_signals[0] = Signal(
        type=SignalType.URL,
        value="the campaign editor route for the new campaign",
        value_predicate="/Box/Editor/{campaign_id:numeric}",
        provenance=_provenance(), confidence=1.0, status=Status.BELIEVED,
    )
    p = render_regression_prompt(kf)
    assert "/Box/Editor/{campaign_id:numeric}" in p
    low = p.lower()
    assert "fact (confirm in this exact shape" in low
    assert "must match that\ntemplate exactly" in low or "must match that template exactly" in low


def test_parse_executor_result_stamps_missing_provenance() -> None:
    """The agent emits what it SAW (kind / type / value / present); the SYSTEM
    stamps provenance. An observation without source_type / source_id is accepted
    and attributed to the run's agent identity (ADR-0008: source_id =
    agent_identity; AGENTS.md: the agent does not invent a generated id). This is
    what lets the claude -p brain return minimal observations without a pydantic
    ValidationError. An explicit provenance still wins."""
    from praxis.runner.regression import _parse_executor_result

    raw = {
        "observations": [{
            "kind": "success", "type": "behavioral",
            "value": "dashboard renders", "present": True,
        }],
        "actions": 2,
    }
    ctx = _parse_executor_result(raw, agent_id="praxis-cli")
    assert len(ctx.observations) == 1
    assert ctx.observations[0].source_type.value == "agent"
    assert ctx.observations[0].source_id == "praxis-cli"

    explicit = {"observations": [{
        "kind": "success", "type": "behavioral", "value": "x", "present": True,
        "source_type": "human", "source_id": "pablo",
    }]}
    ctx2 = _parse_executor_result(explicit, agent_id="praxis-cli")
    assert ctx2.observations[0].source_id == "pablo"


def test_parse_executor_result_carries_structured_observed_payload() -> None:
    """ADR-0031: an observation's `observed` payload (the raw before/after counts
    or identifier+membership the agent self-reports) rides through the executor
    parse onto ObservedSignal, so the matcher can evaluate the check over it."""
    from praxis.runner.regression import _parse_executor_result

    raw = {"observations": [{
        "kind": "success", "type": "network",
        "value": "the list went from 15 to 14", "present": True,
        "observed": {"before_count": 15, "after_count": 14},
    }]}
    ctx = _parse_executor_result(raw, agent_id="praxis-cli")
    assert ctx.observations[0].observed == {"before_count": 15, "after_count": 14}


def test_regression_prompt_renders_structured_check_instructions() -> None:
    """ADR-0031: a check signal surfaces a `structured check (...)` line telling
    the agent to emit the raw `observed` payload, not free prose."""
    from praxis.model import ElementMembershipCheck, ListCountDeltaCheck

    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    prov = Provenance(source_type=SourceType.HUMAN, source_id="pablo-seed",
                      last_verified=now, observation_count=1)
    kf = KnowledgeFile(
        schema_version="0", goal_id="delete-a-campaign",
        goal="a user can archive a campaign", target=Target(app="digioh"),
        success_signals=[
            Signal(type=SignalType.NETWORK, value="one fewer campaign",
                   check=ListCountDeltaCheck(expect_delta=-1),
                   provenance=prov, confidence=1.0, status=Status.BELIEVED),
            Signal(type=SignalType.NETWORK, value="archived id gone",
                   check=ElementMembershipCheck(identifier_slot="campaign_id",
                                                expect="absent"),
                   provenance=prov, confidence=1.0, status=Status.BELIEVED),
        ],
        meta=Meta(created_at=now, updated_at=now),
    )
    p = render_regression_prompt(kf, budget_tokens=5000)
    assert "structured check" in p
    assert "before_count" in p and "after_count" in p
    assert "campaign_id" in p and "present" in p
    # The emit contract tells the agent not to self-judge the check.
    assert "the runner evaluates the check" in p


def test_exploration_prompt_includes_risks_with_structured_triggers() -> None:
    kf = _login_kf()
    p = render_exploration_prompt(kf, budget_tokens=5000)
    assert "EXPLORATION" in p
    # Structured trigger forms rendered deterministically:
    assert "SEQUENCE 4x submit wrong password" in p
    assert "HTTP POST /cart/apply" in p
    # The body/params should be JSON-encoded so it is unambiguous in the prompt.
    assert '"coupon": "SAVE10"' in p


def test_exploration_prompt_omits_quarantined_risks() -> None:
    kf = _login_kf()
    # Force a risk to quarantined and verify it disappears from the prompt.
    kf.risks[0].status = Status.QUARANTINED  # type: ignore[index]
    p = render_exploration_prompt(kf)
    assert "lockout" not in p


# --- runner ----------------------------------------------------------------


def _seeded_adapter(kf: KnowledgeFile, dirpath: Path) -> BrowserUseAdapter:
    store = FileEventStore(str(dirpath))
    return BrowserUseAdapter(store, target=kf.target, seeds={kf.goal_id: kf})


def _confirming_executor(prompt: str) -> dict:
    assert "GOAL (login)" in prompt
    return {
        "observations": [
            ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                           value="sign-out action becomes available",
                           source_type=SourceType.AGENT,
                           source_id="praxis-regress"),
            ObservedSignal(kind="success", type=SignalType.NETWORK,
                           value="POST /session returns 2xx and sets a session cookie",
                           source_type=SourceType.AGENT,
                           source_id="praxis-regress"),
        ],
        "actions": 5,
        "tokens": 1234,
        "notes": ["ran the happy path cleanly"],
    }


def test_runner_run_one_computes_verdict_without_growing_the_oracle() -> None:
    """ADR-0029 defect A: regress is a READ of the believed oracle, not a writer of
    promotable evidence. run_one computes the verdict in-memory from the agent's
    observations but, by default, does NOT persist them as promotable
    ObservationEvents, so a confirmation run cannot self-certify the oracle. The
    store stays empty (only the seed supplies the oracle) and the believed success
    set is unchanged after the run."""
    kf = _login_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))

        believed_before = {
            s.value for s in adapter.read_knowledge("login").success_signals  # type: ignore[union-attr]
            if s.status == Status.BELIEVED
        }

        runner = RegressionRunner(adapter)
        result = runner.run_one("login", _confirming_executor, budget_tokens=5000)

        assert result.verdict == RegressionVerdict.PASS
        assert result.actions == 5
        assert result.tokens == 1234
        assert result.notes == ["ran the happy path cleanly"]
        # The verdict was reached, but NO promotable observation landed in the store.
        events = list(adapter.store.read("login"))
        assert events == []
        # The believed success set is exactly what the seed defined - regress did
        # not grow it.
        believed_after = {
            s.value for s in adapter.read_knowledge("login").success_signals  # type: ignore[union-attr]
            if s.status == Status.BELIEVED
        }
        assert believed_after == believed_before


def test_n_regress_runs_leave_the_believed_success_set_unchanged() -> None:
    """ADR-0029 defect A, the inflation guard: N regress runs over a seeded goal must
    leave the believed success set IDENTICAL to the seed's. This is the
    create-welcome-popup failure mode (4 seeded signals growing to 26 agent-sourced
    ones); after the fix the believed set never grows, no matter how many runs."""
    kf = _login_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        seed_believed = {
            s.value for s in adapter.read_knowledge("login").success_signals  # type: ignore[union-attr]
            if s.status == Status.BELIEVED
        }

        runner = RegressionRunner(adapter)
        for _ in range(26):
            r = runner.run_one("login", _confirming_executor, budget_tokens=5000)
            assert r.verdict == RegressionVerdict.PASS

        # No events accumulated and the believed set is unchanged after 26 runs.
        assert list(adapter.store.read("login")) == []
        believed_after = {
            s.value for s in adapter.read_knowledge("login").success_signals  # type: ignore[union-attr]
            if s.status == Status.BELIEVED
        }
        assert believed_after == seed_believed


def test_runner_persist_observations_opt_in_still_writes() -> None:
    """The persist path is not removed, only defaulted OFF for regress: an explicit
    opt-in (a non-regress caller that genuinely wants to seed the store) still
    appends the event. This pins that the flag, not a deleted code path, is what
    changed."""
    kf = _login_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = RegressionRunner(adapter)
        runner.run_one("login", _confirming_executor, budget_tokens=5000,
                       persist_observations=True)
        events = list(adapter.store.read("login"))
        assert len(events) == 1
        assert events[0].agent_id == "praxis-regress"


def test_runner_unknown_goal_raises() -> None:
    kf = _login_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = RegressionRunner(adapter)
        with pytest.raises(ValueError):
            runner.run_one("nonexistent", lambda _: {"observations": []})


def test_runner_stop_on_fail() -> None:
    kf = _login_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))

        def fail_executor(prompt: str) -> dict:
            return {
                "observations": [
                    ObservedSignal(kind="failure", type=SignalType.TEXT,
                                   value="invalid credentials banner",
                                   source_type=SourceType.AGENT, source_id="r"),
                ],
                "actions": 1, "tokens": 0,
            }

        runner = RegressionRunner(adapter)
        results = runner.run_all(["login", "login"], fail_executor, stop_on_fail=True)
        assert len(results) == 1  # stopped after first fail
        assert results[0].verdict == RegressionVerdict.FAIL


# --- reports ---------------------------------------------------------------


def _result(goal: str, verdict: RegressionVerdict, **kw: object):
    from praxis.runner.regression import RunResult
    return RunResult(
        goal_id=goal, verdict=verdict,
        actions=kw.get("actions", 1),  # type: ignore[arg-type]
        tokens=kw.get("tokens"),  # type: ignore[arg-type]
        wall_seconds=kw.get("wall_seconds", 0.5),  # type: ignore[arg-type]
        matched_success=kw.get("matched_success", []),  # type: ignore[arg-type]
        matched_failure=kw.get("matched_failure", []),  # type: ignore[arg-type]
        notes=kw.get("notes", []),  # type: ignore[arg-type]
    )


def test_junit_xml_marks_failures_and_uncertain() -> None:
    results = [
        _result("login", RegressionVerdict.PASS, actions=5, tokens=1000),
        _result("checkout", RegressionVerdict.FAIL,
                matched_failure=["double-order created"]),
        _result("settings", RegressionVerdict.UNCERTAIN),
    ]
    with tempfile.TemporaryDirectory() as td:
        out = write_junit_xml(results, Path(td) / "x.xml")
        text = out.read_text()
        assert "<testsuite" in text
        assert 'tests="3"' in text
        assert 'failures="1"' in text
        assert 'skipped="1"' in text
        assert "<failure" in text
        assert "<skipped" in text
        assert "double-order created" in text


def test_markdown_report_has_summary_and_per_goal_rows() -> None:
    results = [
        _result("login", RegressionVerdict.PASS, actions=5, tokens=1000,
                matched_success=["a", "b"]),
        _result("checkout", RegressionVerdict.FAIL,
                matched_failure=["double-order created"]),
    ]
    with tempfile.TemporaryDirectory() as td:
        out = write_markdown_report(results, Path(td) / "x.md")
        text = out.read_text()
        assert "# praxis regress" in text
        assert "1 pass / 1 fail" in text
        assert "`login`" in text and "**pass**" in text
        assert "`checkout`" in text and "**fail**" in text
        assert "double-order created" in text
