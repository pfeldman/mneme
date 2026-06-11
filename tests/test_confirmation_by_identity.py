"""ADR-0033: enumerated signals are confirmed by identity with mandatory
evidence (offline; no browser, no claude).

The fixtures for the binding tests are the TWO LIVE SIGNALS from the 0.0.3
release-blocker incident (tasks/signal-matching-redesign/analysis.md): both
were confirmed `present: true` by the agent with honest, enriched evidence and
both were rejected by the free-text Jaccard arm (0.400 / 0.409 < 0.5),
flipping a healthy app to a false REGRESSED. Under ADR-0033 they bind by ref
and the goal reads OK by construction; these tests pin that live bug dead.

Coverage map:
  refs bind with NO Jaccard involvement .... test_live_examples_*
  empty evidence VOIDs loudly .............. test_empty_evidence_*
  unknown ref VOIDs loudly ................. test_unknown_ref_*
  duplicate ref VOIDs ...................... test_duplicate_ref_*
  check still gates, fail-closed ........... test_check_target_*
  predicate evaluates over EVIDENCE ........ test_predicate_*
  present:false leaves unconfirmed ......... test_present_false_*
  failure ref fires FAIL ................... test_failure_ref_*
  legacy envelope falls back, flagged ...... test_legacy_envelope_*
  advisory tripwires flag, never gate ...... test_tripwire_*
  flags + voids land in the audit record ... test_flags_and_voids_persist_*
  end to end: live goal -> PASS -> OK ...... test_end_to_end_*
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from praxis.adapters import BrowserUseAdapter
from praxis.model import (
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
from praxis.runner import AggregateVerdict, RegressionVerdict, RunResult
from praxis.runner import regression as regression_mod
from praxis.runner.regression import (
    RegressionRunner,
    bind_confirmations,
    classify_goal,
    run_aggregate,
    verdict_from_observations,
)
from praxis.store import FileEventStore

# --- the two LIVE signals (analysis doc section 1) --------------------------

_BEHAVIORAL_SEED = (
    "the Digioh Agent panel returns a chat answer that identifies the user's "
    "account (for example, states the account name), rather than an error or "
    "a refusal"
)
_NETWORK_SEED = (
    "submitting the question triggers a POST to the chat turn endpoint "
    "(lightbox-mcpchat-node-dev2.azurewebsites.net/chat/turn) that returns a "
    "2xx response"
)
_BEHAVIORAL_EVIDENCE = (
    "The Digioh Agent panel returned the chat answer 'Your account name is "
    "Test Account (User ID 46174).', identifying the account by name rather "
    "than erroring or refusing."
)
_NETWORK_EVIDENCE = (
    "Submitting the question triggered a POST to "
    "https://lightbox-mcpchat-node-dev2.azurewebsites.net/chat/turn that "
    "returned a 200 OK response."
)
_FAILURE_SEED = "the panel shows an error message refusing to answer"


def _provenance() -> Provenance:
    return Provenance(
        source_type=SourceType.HUMAN,
        source_id="pablo",
        last_verified=datetime(2026, 6, 11, tzinfo=timezone.utc),
        observation_count=1,
    )


def _signal(type_: SignalType, value: str, **kw: Any) -> Signal:
    return Signal(
        type=type_, value=value, provenance=_provenance(),
        confidence=1.0, status=Status.BELIEVED, **kw,
    )


def _live_kf() -> KnowledgeFile:
    return KnowledgeFile(
        schema_version="0",
        goal_id="agent-panel-answers",
        goal="the Digioh Agent panel answers an account question",
        target=Target(app="digioh"),
        success_signals=[
            _signal(SignalType.BEHAVIORAL, _BEHAVIORAL_SEED),
            _signal(SignalType.NETWORK, _NETWORK_SEED),
        ],
        failure_signals=[_signal(SignalType.TEXT, _FAILURE_SEED)],
        meta=Meta(created_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
                  updated_at=datetime(2026, 6, 11, tzinfo=timezone.utc)),
    )


def _live_confirmations() -> list[dict[str, Any]]:
    return [
        {"ref": "S1", "present": True, "evidence": _BEHAVIORAL_EVIDENCE},
        {"ref": "S2", "present": True, "evidence": _NETWORK_EVIDENCE},
    ]


# --- binding: identity, no Jaccard ------------------------------------------


def test_live_examples_confirm_by_ref_with_no_jaccard(monkeypatch: Any) -> None:
    """The two LIVE signals both bind by ref and the goal PASSES, with the
    legacy matcher never consulted (ADR-0033 decision 1: for an enumerated
    seed there is NO fuzzy matching). `_value_matches` is replaced with a
    tripwire that raises, so any Jaccard involvement fails the test."""
    kf = _live_kf()
    bound, voids = bind_confirmations(kf, _live_confirmations(), agent_id="a1")
    assert voids == []
    assert len(bound) == 2
    # Decision 2: the seed's declared type and value are SYSTEM-STAMPED; the
    # agent's words live in `evidence` only.
    assert bound[0].type == SignalType.BEHAVIORAL
    assert bound[0].value == _BEHAVIORAL_SEED
    assert bound[0].evidence == _BEHAVIORAL_EVIDENCE
    assert bound[1].type == SignalType.NETWORK
    assert bound[1].value == _NETWORK_SEED

    def _boom(*_a: Any, **_k: Any) -> bool:
        raise AssertionError(
            "Jaccard/legacy matching must NOT run for a ref-bound confirmation"
        )

    monkeypatch.setattr(regression_mod, "_value_matches", _boom)
    verdict, ok, bad = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.PASS
    assert set(ok) == {_BEHAVIORAL_SEED, _NETWORK_SEED}
    assert bad == []


def test_live_examples_would_still_fail_the_legacy_jaccard_arm() -> None:
    """The control: the SAME live evidence, fed as an unsolicited free-text
    observation (the pre-ADR-0033 shape), still fails the unchanged Jaccard
    floor - which is exactly the live bug. Identity binding, not a loosened
    matcher, is what fixed it (forbidden alternative: no threshold tuning)."""
    from praxis.store import ObservedSignal

    kf = _live_kf()
    legacy = [
        ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                       value=_BEHAVIORAL_EVIDENCE,
                       source_type=SourceType.AGENT, source_id="a1"),
        ObservedSignal(kind="success", type=SignalType.NETWORK,
                       value=_NETWORK_EVIDENCE,
                       source_type=SourceType.AGENT, source_id="a1"),
    ]
    verdict, ok, _ = verdict_from_observations(kf, legacy)
    assert verdict == RegressionVerdict.UNCERTAIN
    assert ok == []  # both live confirmations bounce off the 0.5 floor


# --- decision 4: malformed confirmations are VOID and loud ------------------


def test_empty_evidence_voids_loudly() -> None:
    """present:true with empty/missing evidence is VOID: the signal stays
    unconfirmed (fail closed), the void is named with its ref, and the bound
    observation rides flagged `void:*` so the record keeps it."""
    kf = _live_kf()
    confs = [
        {"ref": "S1", "present": True, "evidence": "   "},
        {"ref": "S2", "present": True},  # evidence missing entirely
    ]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert len(voids) == 2
    assert any("S1" in v and "empty" in v for v in voids)
    assert any("S2" in v and "empty" in v for v in voids)
    assert all(any(f.startswith("void:") for f in (o.flags or [])) for o in bound)
    verdict, ok, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.UNCERTAIN
    assert ok == []  # a void is never a green


def test_void_is_named_in_the_run_output() -> None:
    """The GoalReport.evidence names the void and its reason (decision 4), so
    a REGRESSED produced by a sloppy envelope is distinguishable from one
    produced by the app, from the run output alone."""
    kf = _live_kf()
    result = RunResult(
        goal_id=kf.goal_id, verdict=RegressionVerdict.UNCERTAIN,
        actions=1, tokens=None, wall_seconds=0.1,
        void_confirmations=["S1: present:true with empty/missing evidence"],
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.REGRESSED
    assert "void confirmation(s)" in report.evidence
    assert "S1: present:true with empty/missing evidence" in report.evidence


def test_unknown_ref_voids_loudly() -> None:
    kf = _live_kf()
    confs = [{"ref": "S9", "present": True, "evidence": "saw something"}]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert bound == []  # no seed to stamp: nothing binds
    assert len(voids) == 1 and "S9" in voids[0]
    verdict, ok, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.UNCERTAIN and ok == []


def test_duplicate_ref_with_conflicting_present_voids_all_answers() -> None:
    """Duplicate refs with conflicting `present` values void EVERY answer for
    that ref (decision 4): the envelope contradicts itself and a void is never
    a green."""
    kf = _live_kf()
    confs = [
        {"ref": "S1", "present": True, "evidence": _BEHAVIORAL_EVIDENCE},
        {"ref": "S1", "present": False, "evidence": ""},
        {"ref": "S2", "present": True, "evidence": _NETWORK_EVIDENCE},
    ]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert any("S1" in v and "conflicting" in v for v in voids)
    verdict, ok, _ = verdict_from_observations(kf, bound)
    # S1 is void (unconfirmed) even though one of its answers said present.
    assert verdict == RegressionVerdict.UNCERTAIN
    assert ok == [_NETWORK_SEED]


def test_redundant_duplicate_keeps_the_first_answer() -> None:
    """An agreeing duplicate carries no new claim: the first answer counts,
    the redundant copy is void-flagged (loud, traceable), and the signal stays
    confirmed exactly once."""
    kf = _live_kf()
    confs = [
        {"ref": "S1", "present": True, "evidence": _BEHAVIORAL_EVIDENCE},
        {"ref": "S1", "present": True, "evidence": _BEHAVIORAL_EVIDENCE},
        {"ref": "S2", "present": True, "evidence": _NETWORK_EVIDENCE},
    ]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert any("S1" in v and "redundant" in v for v in voids)
    verdict, ok, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.PASS
    assert set(ok) == {_BEHAVIORAL_SEED, _NETWORK_SEED}


# --- decision 3: identity replaces the BINDING, never the grounding ---------


def _check_kf() -> KnowledgeFile:
    kf = _live_kf()
    kf.success_signals = [
        _signal(SignalType.NETWORK, "one fewer campaign after archiving",
                check=ListCountDeltaCheck(expect_delta=-1)),
    ]
    return kf


def test_check_target_still_evaluates_fail_closed() -> None:
    """A ref cannot bypass a failing structured check (ADR-0031 unchanged):
    the confirmation must carry the `observed` payload AND the check must hold
    over it. A no-op delta or a missing payload is VOID, never a match."""
    kf = _check_kf()
    # Failing payload: the archive did not remove (delta 0 vs expected -1).
    bound, voids = bind_confirmations(kf, [{
        "ref": "S1", "present": True, "evidence": "list went 15 to 15",
        "observed": {"before_count": 15, "after_count": 15},
    }], agent_id="a1")
    assert any("S1" in v and "check" in v for v in voids)
    verdict, ok, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.UNCERTAIN and ok == []

    # A lazy tick with NO payload at all also fails closed.
    bound2, voids2 = bind_confirmations(kf, [{
        "ref": "S1", "present": True, "evidence": "looked fine",
    }], agent_id="a1")
    assert any("S1" in v for v in voids2)
    verdict2, ok2, _ = verdict_from_observations(kf, bound2)
    assert verdict2 == RegressionVerdict.UNCERTAIN and ok2 == []

    # The genuine confirmation (real counts satisfying the delta) passes.
    bound3, voids3 = bind_confirmations(kf, [{
        "ref": "S1", "present": True, "evidence": "list went 15 to 14",
        "observed": {"before_count": 15, "after_count": 14},
    }], agent_id="a1")
    assert voids3 == []
    verdict3, ok3, _ = verdict_from_observations(kf, bound3)
    assert verdict3 == RegressionVerdict.PASS and len(ok3) == 1


def test_predicate_evaluates_over_the_evidence_string() -> None:
    """A `value_predicate` target evaluates over the EVIDENCE (ADR-0030
    semantics, ADR-0033 decision 3), fail-closed: evidence carrying the
    invariant with a filled, shaped slot confirms; evidence without it is
    VOID. The ref never substitutes for the predicate."""
    kf = _live_kf()
    kf.success_signals = [
        _signal(SignalType.URL, "the campaign editor route for the new campaign",
                value_predicate="/Box/Editor/{campaign_id:numeric}"),
    ]
    good, voids = bind_confirmations(kf, [{
        "ref": "S1", "present": True,
        "evidence": "after saving, the route matches /Box/Editor/329419",
    }], agent_id="a1")
    assert voids == []
    verdict, ok, _ = verdict_from_observations(kf, good)
    assert verdict == RegressionVerdict.PASS and len(ok) == 1

    bad, voids2 = bind_confirmations(kf, [{
        "ref": "S1", "present": True, "evidence": "the editor opened fine",
    }], agent_id="a1")
    assert any("S1" in v and "predicate" in v for v in voids2)
    verdict2, ok2, _ = verdict_from_observations(kf, bad)
    assert verdict2 == RegressionVerdict.UNCERTAIN and ok2 == []


def test_present_false_leaves_the_signal_unconfirmed() -> None:
    """present:false is an honest negative: no void, no match, the signal is
    unconfirmed and the goal routes through the existing UNCERTAIN path."""
    kf = _live_kf()
    confs = [
        {"ref": "S1", "present": False, "evidence": ""},
        {"ref": "S2", "present": True, "evidence": _NETWORK_EVIDENCE},
    ]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert voids == []
    verdict, ok, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.UNCERTAIN
    assert ok == [_NETWORK_SEED]


def test_failure_ref_confirmed_present_fires_fail() -> None:
    kf = _live_kf()
    confs = [{
        "ref": "F1", "present": True,
        "evidence": "the panel showed 'I cannot answer that' and no account name",
    }]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert voids == []
    verdict, _, bad = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.FAIL
    assert bad == [_FAILURE_SEED]


# --- decision 7: legacy envelope falls back, flagged -------------------------


def _seeded_adapter(kf: KnowledgeFile, dirpath: Path) -> BrowserUseAdapter:
    store = FileEventStore(str(dirpath))
    return BrowserUseAdapter(store, target=kf.target, seeds={kf.goal_id: kf})


def _paraphrase_kf() -> KnowledgeFile:
    kf = _live_kf()
    kf.success_signals = [
        _signal(SignalType.BEHAVIORAL, "a sign-out action becomes available"),
    ]
    return kf


def test_legacy_envelope_without_confirmations_matches_via_jaccard_flagged() -> None:
    """An old envelope (observations only, no `confirmations` key) still
    parses and matches through the unchanged Jaccard path; the run flags that
    the goal was matched by paraphrase (one transition release)."""
    kf = _paraphrase_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = RegressionRunner(adapter)

        def legacy_executor(prompt: str) -> dict:
            return {"observations": [{
                "kind": "success", "type": "behavioral",
                "value": "sign-out becomes available", "present": True,
            }], "actions": 2}

        result = runner.run_one(kf.goal_id, legacy_executor)
        assert result.verdict == RegressionVerdict.PASS
        assert result.paraphrase_matched is True
        report = classify_goal(kf, result)
        assert report.verdict == AggregateVerdict.OK
        assert "matched by paraphrase" in report.evidence


def test_ref_bound_run_is_not_flagged_as_paraphrase() -> None:
    kf = _live_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = RegressionRunner(adapter)

        def executor(prompt: str) -> dict:
            return {"confirmations": _live_confirmations(),
                    "observations": [], "actions": 3}

        result = runner.run_one(kf.goal_id, executor)
        assert result.verdict == RegressionVerdict.PASS
        assert result.paraphrase_matched is False
        report = classify_goal(kf, result)
        assert report.verdict == AggregateVerdict.OK
        assert "matched by paraphrase" not in report.evidence


# --- decision 5: advisory tripwires flag, never gate -------------------------


def test_tripwire_parrot_evidence_flags_but_verdict_unchanged() -> None:
    """Evidence that adds zero content tokens beyond the seed (a copy of the
    prompt) trips the parrot flag; the verdict is UNCHANGED by the flag."""
    kf = _live_kf()
    confs = [
        {"ref": "S1", "present": True, "evidence": _BEHAVIORAL_SEED},  # parrot
        {"ref": "S2", "present": True, "evidence": _NETWORK_EVIDENCE},
    ]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert voids == []
    assert "parrot-evidence" in (bound[0].flags or [])
    assert "parrot-evidence" not in (bound[1].flags or [])
    verdict, ok, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.PASS  # flag, not gate
    assert len(ok) == 2


def test_tripwire_offtopic_evidence_flags_but_verdict_unchanged() -> None:
    """Evidence whose containment vs the seed sits below the 0.15 floor (the
    analysis doc's measured calibration) trips the off-topic flag; the verdict
    is UNCHANGED."""
    kf = _live_kf()
    confs = [
        {"ref": "S1", "present": True,
         "evidence": "the page loaded fine with no console errors"},  # 0.0
        {"ref": "S2", "present": True, "evidence": _NETWORK_EVIDENCE},
    ]
    bound, voids = bind_confirmations(kf, confs, agent_id="a1")
    assert voids == []
    assert "off-topic-evidence" in (bound[0].flags or [])
    assert "off-topic-evidence" not in (bound[1].flags or [])
    verdict, _, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.PASS


def test_tripwire_honest_live_evidence_is_clean() -> None:
    """The two LIVE evidence strings sit in the honest middle band (containment
    0.56+ against a 0.15 floor; 6+ novel tokens): neither end flags."""
    kf = _live_kf()
    bound, _ = bind_confirmations(kf, _live_confirmations(), agent_id="a1")
    for o in bound:
        assert "parrot-evidence" not in (o.flags or [])
        assert "off-topic-evidence" not in (o.flags or [])
        assert "type-vocabulary" not in (o.flags or [])


def test_tripwire_network_type_vocabulary_flags() -> None:
    """A `network` confirmation whose evidence names neither a status-shaped
    nor a URL-shaped token trips the type-vocabulary flag (advisory)."""
    kf = _live_kf()
    confs = [
        {"ref": "S1", "present": True, "evidence": _BEHAVIORAL_EVIDENCE},
        {"ref": "S2", "present": True,
         "evidence": "submitting the question triggered the chat turn call "
                     "and the response came back"},
    ]
    bound, _ = bind_confirmations(kf, confs, agent_id="a1")
    assert "type-vocabulary" in (bound[1].flags or [])
    verdict, _, _ = verdict_from_observations(kf, bound)
    assert verdict == RegressionVerdict.PASS  # flag, never a gate


def test_flags_and_voids_persist_in_the_regress_record() -> None:
    """The per-signal {ref, present, evidence, flags} and the run's void
    reasons land in the existing NON-PROMOTABLE RegressObservationEvent
    (decision 5; no second record kind), so every confirmation is auditable
    forever - and the record never grows the believed set (ADR-0029)."""
    kf = _live_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = RegressionRunner(adapter)

        def executor(prompt: str) -> dict:
            return {"confirmations": [
                {"ref": "S1", "present": True, "evidence": _BEHAVIORAL_SEED},
                {"ref": "S2", "present": True, "evidence": ""},  # void
                {"ref": "S7", "present": True, "evidence": "x"},  # unknown ref
            ], "actions": 1}

        result = runner.run_one(kf.goal_id, executor)
        # Voids surfaced loud in the run output.
        assert any("S2" in v for v in result.void_confirmations)
        assert any("S7" in v for v in result.void_confirmations)
        assert any("parrot-evidence" in f for f in result.confirmation_flags)
        assert result.verdict == RegressionVerdict.UNCERTAIN  # S2 unconfirmed

        records = adapter.store.read_regress(kf.goal_id)
        assert len(records) == 1
        rec = records[0]
        by_ref = {s.ref: s for s in rec.signals if s.ref is not None}
        # The parrot flag is on the persisted S1 signal; the verdict it could
        # not change is stored alongside.
        assert "parrot-evidence" in (by_ref["S1"].flags or [])
        assert by_ref["S1"].evidence  # the evidence is on the record forever
        assert by_ref["S1"].value == _BEHAVIORAL_SEED  # system-stamped echo
        # The void empty-evidence answer rides flagged void:*.
        assert any(f.startswith("void:") for f in (by_ref["S2"].flags or []))
        # The unbindable void (unknown ref) is named in the record's voids.
        assert rec.voids and any("S7" in v for v in rec.voids)
        # NON-PROMOTABLE: the promotable stream stays empty.
        assert list(adapter.store.read(kf.goal_id)) == []


# --- end to end: the live bug pinned dead ------------------------------------


def test_end_to_end_live_goal_confirmed_by_ref_is_ok(monkeypatch: Any) -> None:
    """The 0.0.3 release blocker, end to end: a goal whose two believed
    signals are confirmed by ref with paraphrased, enriched evidence reaches
    PASS and the aggregate classifies OK. No paraphrase, conjugation, or URL
    phrasing can flip the verdict on an enumerated seed."""
    kf = _live_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = RegressionRunner(adapter)

        def brain(prompt: str) -> dict:
            # The brain answers the refs the prompt enumerated.
            assert "S1." in prompt and "S2." in prompt and "F1." in prompt
            return {"confirmations": _live_confirmations(),
                    "observations": [], "actions": 4, "tokens": 800}

        reports = run_aggregate(runner, [kf.goal_id], brain)
        assert len(reports) == 1
        assert reports[0].verdict == AggregateVerdict.OK
        assert not reports[0].fails_run
        # The believed set did not grow from the confirmation run (ADR-0029 /
        # ADR-0033 forbidden alternative).
        assert list(adapter.store.read(kf.goal_id)) == []


def test_end_to_end_genuinely_broken_app_still_routes_loud() -> None:
    """The same envelope under a genuinely broken app (the agent honestly
    reports present:false / fires the failure ref) routes to REGRESSED exactly
    as today; nothing in ADR-0033 touches that path."""
    kf = _live_kf()
    with tempfile.TemporaryDirectory() as td:
        adapter = _seeded_adapter(kf, Path(td))
        runner = RegressionRunner(adapter)

        def broken_brain(prompt: str) -> dict:
            return {"confirmations": [
                {"ref": "S1", "present": False, "evidence": ""},
                {"ref": "S2", "present": False, "evidence": ""},
                {"ref": "F1", "present": True,
                 "evidence": "the panel replied 'I cannot help with that' "
                             "instead of the account name"},
            ], "observations": [], "actions": 4}

        reports = run_aggregate(runner, [kf.goal_id], broken_brain)
        assert reports[0].verdict == AggregateVerdict.REGRESSED
        assert reports[0].fails_run
        assert _FAILURE_SEED in reports[0].signals


# --- envelope model: additive fields ----------------------------------------


def test_observed_signal_confirmation_fields_are_additive() -> None:
    """`ref` / `evidence` / `flags` default None so every pre-ADR-0033
    envelope, event, and fixture is unaffected."""
    from praxis.store import ObservedSignal

    legacy = ObservedSignal(kind="success", type=SignalType.BEHAVIORAL,
                            value="x", source_type=SourceType.AGENT,
                            source_id="a")
    assert legacy.ref is None and legacy.evidence is None and legacy.flags is None


def test_parse_executor_result_distinguishes_legacy_from_empty_confirmations() -> None:
    """The parser keeps the raw confirmations for the runner to bind and
    distinguishes a LEGACY envelope (no key) from a new envelope that
    confirmed nothing (empty array), so the decision-7 fallback flag can be
    accurate."""
    from praxis.runner.regression import _parse_executor_result

    legacy = _parse_executor_result({"observations": []})
    assert legacy.has_confirmations is False and legacy.raw_confirmations == []

    new = _parse_executor_result({"observations": [], "confirmations": []})
    assert new.has_confirmations is True and new.raw_confirmations == []

    filled = _parse_executor_result({
        "observations": [],
        "confirmations": [{"ref": "S1", "present": True, "evidence": "e"}],
    })
    assert filled.raw_confirmations == [
        {"ref": "S1", "present": True, "evidence": "e"}
    ]


def test_preamble_documents_the_confirmation_contract() -> None:
    """The console brain preamble (the one envelope producer) names the
    confirmations array, the mandatory evidence, and the never-tick guardrail,
    and keeps the structured-check `observed` shapes (ADR-0031)."""
    from praxis.cli.claude_brain import _HEADLESS_PREAMBLE

    assert '"confirmations"' in _HEADLESS_PREAMBLE
    assert '"ref"' in _HEADLESS_PREAMBLE and '"evidence"' in _HEADLESS_PREAMBLE
    assert "MANDATORY" in _HEADLESS_PREAMBLE
    assert "NEVER tick" in _HEADLESS_PREAMBLE
    assert "never fabricate" in _HEADLESS_PREAMBLE
    # The agent never restates seed text (decision 2).
    assert "binds your answer to the signal by the ref" in _HEADLESS_PREAMBLE


@pytest.mark.parametrize("entry", [
    "not-a-dict",
    {"present": True, "evidence": "no ref at all"},
    {"ref": 7, "present": True, "evidence": "non-string ref"},
])
def test_malformed_confirmation_entries_void_not_crash(entry: Any) -> None:
    kf = _live_kf()
    bound, voids = bind_confirmations(kf, [entry], agent_id="a1")
    assert bound == []
    assert len(voids) == 1
