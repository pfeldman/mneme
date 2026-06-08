"""Aggregate (default-all) regress + break-vs-drift verdict tests (ADR-0023).

ADR-0023 decisions 2 through 7 fix the contract this file pins:

- Decision 2: no `--goal` runs EVERY believed goal and emits ONE aggregate
  report under `.praxis/runs/<timestamp>/`.
- Decision 3: the per-goal verdict is exactly one of OK / REGRESSED / STALE,
  shipped with its evidence. REGRESSED vs STALE is the break-vs-drift
  distinction (file a bug vs re-seed the knowledge).
- Decision 4: a REGRESSED or ERROR goal fails the run LOUDLY (non-zero exit,
  named goal + named signal). One REGRESSED never hides behind a "mostly green"
  roll-up. A goal that ERRORS is non-OK and fails the run, never silently
  skipped, never counted OK.
- Decision 6: R-mode keeps the ADR-0009 no-auditor-input closure.
- Decision 7: per-goal budget slice (tokens + wall time), not one shared pool;
  a goal that exhausts its slice is a loud ERROR for that goal.

Coverage map (the handoff's verification list):
  OK / REGRESSED / STALE each covered ............. test_classify_* + the
                                                    end-to-end aggregate tests.
  one REGRESSED -> non-zero exit + named signal ... test_single_regressed_*.
  errored goal is non-OK and fails the run ........ test_errored_goal_*.
  per-goal budget exhaustion is a loud ERROR ...... test_budget_exhaustion_*.
  auditor scenarios are not an input .............. test_auditor_scenarios_*.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from praxis.cli.main import discover_project
from praxis.cli.main import main as cli_run
from praxis.model import (
    AuthState,
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
)
from praxis.runner import (
    AggregateVerdict,
    GoalReport,
    RegressionVerdict,
    RunResult,
    aggregate_run_failed,
    classify_goal,
    to_aggregate_markdown,
)
from praxis.runner.regression import (
    RegressionRunner,
    _goal_version_anchor,
    run_aggregate,
)
from praxis.skill_driver import regress_aggregate_via_skill


# --- seed helpers ---------------------------------------------------------


def _seed_yaml(goal_id: str, *, anchor_version: str | None = None) -> str:
    """A minimal valid two-signal seed (behavioral success + a failure signal).

    `anchor_version` stamps the success signal's provenance with an
    `observed_app_version`; the projection only demotes a believed signal to
    STALE when `current_version` is set AND not in the observed set, so a seed
    with no version stays believed regardless of the live version (used for the
    OK / REGRESSED end-to-end goals).
    """
    anchor = (
        f'\n      observed_app_version: "{anchor_version}"'
        if anchor_version else ""
    )
    return f"""\
schema_version: "0"
goal_id: {goal_id}
goal: goal {goal_id}
target:
  app: testapp
  environment: local
success_signals:
  - type: behavioral
    value: a Sign out control is present after submitting valid credentials
    confidence: 1.0
    status: believed
    provenance:
      source_type: human
      source_id: pablo
      last_verified: "2026-06-07T00:00:00Z"
      observation_count: 1{anchor}
failure_signals:
  - type: text
    value: an invalid credentials banner appears
    confidence: 1.0
    status: believed
    provenance:
      source_type: human
      source_id: pablo
      last_verified: "2026-06-07T00:00:00Z"
      observation_count: 1
meta:
  created_at: "2026-06-07T00:00:00Z"
  updated_at: "2026-06-07T00:00:00Z"
"""


def _init_with_goals(root: Path, goals: list[str]) -> None:
    """init a project under `root` and learn one seed per goal id."""
    old = Path.cwd()
    os.chdir(root)
    try:
        assert cli_run(["init", "--app", "testapp", "--env", "local"]) == 0
        for gid in goals:
            seed = root / f"{gid}.yaml"
            seed.write_text(_seed_yaml(gid))
            assert cli_run(["learn", gid, "--from-file", str(seed)]) == 0
    finally:
        os.chdir(old)


# Brain outputs. A Brain is `Callable[[str], dict]`; these are deterministic
# stand-ins so the surfaces are compared on equal input (ADR-0019).
_PASS_OBS: dict[str, Any] = {
    "observations": [
        {
            "kind": "success",
            "type": "behavioral",
            "value": "a Sign out control is present after submitting valid credentials",
            "source_type": "agent",
            "source_id": "praxis-cli",
        },
    ],
    "actions": 5,
    "tokens": 1000,
    "visited_urls": [],
}

_FAIL_OBS: dict[str, Any] = {
    "observations": [
        {
            "kind": "failure",
            "type": "text",
            "value": "an invalid credentials banner appears",
            "source_type": "agent",
            "source_id": "praxis-cli",
        },
    ],
    "actions": 5,
    "tokens": 1000,
    "visited_urls": [],
}

_ABSENT_SUCCESS_OBS: dict[str, Any] = {
    # No success signal observed and no failure: an UNCERTAIN run. With no
    # benign explanation this is a break (REGRESSED), not drift.
    "observations": [],
    "actions": 5,
    "tokens": 1000,
    "visited_urls": [],
}

_HEALTHY_EQUIVALENT_OBS: dict[str, Any] = {
    # The literal believed success signal is absent, but the brain observed a
    # healthy equivalent of the success path: the app changed on purpose
    # (STALE / drift, route to a re-seed).
    "observations": [],
    "actions": 5,
    "tokens": 1000,
    "visited_urls": [],
    "healthy_equivalent_observed": True,
}


def _const_brain(obs: dict[str, Any]):
    def brain(prompt: str) -> dict[str, Any]:
        return json.loads(json.dumps(obs))
    return brain


# --- classify_goal: OK / REGRESSED / STALE each covered (decision 3) -------


def _believed_kf(goal_id: str, *, anchor_version: str | None = None) -> KnowledgeFile:
    prov = Provenance(
        source_type=SourceType.HUMAN,
        source_id="pablo",
        observed_app_version=anchor_version,
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )
    return KnowledgeFile(
        schema_version="0",
        goal_id=goal_id,
        goal=f"goal {goal_id}",
        target=Target(app="testapp"),
        success_signals=[
            Signal(type=SignalType.BEHAVIORAL,
                   value="a Sign out control is present after submitting valid credentials",
                   provenance=prov, confidence=1.0, status=Status.BELIEVED),
        ],
        failure_signals=[
            Signal(type=SignalType.TEXT,
                   value="an invalid credentials banner appears",
                   provenance=prov, confidence=1.0, status=Status.BELIEVED),
        ],
        meta=Meta(created_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
                  updated_at=datetime(2026, 6, 7, tzinfo=timezone.utc)),
    )


def _run_result(goal_id: str, verdict: RegressionVerdict, **kw: Any) -> RunResult:
    return RunResult(
        goal_id=goal_id,
        verdict=verdict,
        actions=kw.get("actions", 1),
        tokens=kw.get("tokens", 100),
        wall_seconds=kw.get("wall_seconds", 0.1),
        matched_success=kw.get("matched_success", []),
        matched_failure=kw.get("matched_failure", []),
        healthy_equivalent_observed=kw.get("healthy_equivalent_observed", False),
        authenticated=kw.get("authenticated", True),
    )


def _authed_kf(goal_id: str, *, scope: str = "user") -> KnowledgeFile:
    """A believed goal that expects an AUTHENTICATED scope (ADR-0017
    auth_state): authenticated=True with a non-anonymous role. Such a goal is
    AUTH-EXPIRED when the run reports authenticated=False (ADR-0026 decision
    5)."""
    kf = _believed_kf(goal_id)
    kf.auth_state = AuthState(authenticated=True, scope=scope)
    return kf


def _anon_kf(goal_id: str) -> KnowledgeFile:
    """A believed goal whose auth_state is anonymous (no authenticated scope
    expected). It is NEVER AUTH-EXPIRED regardless of the run's authenticated
    flag (the anonymous-scope path is unaffected by ADR-0026)."""
    kf = _believed_kf(goal_id)
    kf.auth_state = AuthState(authenticated=False, scope=None)
    return kf


def test_classify_ok() -> None:
    """A PASS run (all believed success observed, no failure) is OK."""
    kf = _believed_kf("login")
    result = _run_result(
        "login", RegressionVerdict.PASS,
        matched_success=["a Sign out control is present after submitting valid credentials"],
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.OK
    assert report.verdict.is_ok
    assert not report.fails_run


def test_classify_regressed_on_failure_signal() -> None:
    """A FAIL run (a failure signal fired) is REGRESSED and names the signal."""
    kf = _believed_kf("login")
    result = _run_result(
        "login", RegressionVerdict.FAIL,
        matched_failure=["an invalid credentials banner appears"],
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.REGRESSED
    assert report.fails_run
    # The specific signal that flipped is named (decision 3 + 4).
    assert "an invalid credentials banner appears" in report.signals
    assert "an invalid credentials banner appears" in report.evidence


def test_classify_regressed_on_absent_success_signal() -> None:
    """An UNCERTAIN run with a believed success signal absent and NO benign
    explanation is REGRESSED (a missing success path is a break, not silently
    excused as drift). The absent signal is named."""
    kf = _believed_kf("login")
    result = _run_result("login", RegressionVerdict.UNCERTAIN, matched_success=[])
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.REGRESSED
    assert report.fails_run
    assert "a Sign out control is present after submitting valid credentials" in report.signals


def test_classify_stale_on_healthy_equivalent() -> None:
    """An UNCERTAIN run where the brain observed a healthy equivalent of the
    success path is STALE: the app changed on purpose, re-seed the knowledge.
    STALE does not fail the run."""
    kf = _believed_kf("login")
    result = _run_result(
        "login", RegressionVerdict.UNCERTAIN,
        matched_success=[], healthy_equivalent_observed=True,
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.STALE
    assert not report.fails_run
    assert "re-seed" in report.evidence


def test_classify_stale_on_version_anchor_behind() -> None:
    """An UNCERTAIN run whose goal is anchored more than N minors behind the
    live app (ADR-0013 decay anchor) is STALE: the knowledge is pinned to an
    older app version. The anchor and the live version are named."""
    # Goal anchored at 1.0.0; live app at 1.5.0 -> 5 minors back, > default N=2.
    kf = _believed_kf("login", anchor_version="1.0.0")
    result = _run_result("login", RegressionVerdict.UNCERTAIN, matched_success=[])
    report = classify_goal(kf, result, current_version="1.5.0")
    assert report.verdict == AggregateVerdict.STALE
    assert not report.fails_run
    assert "1.0.0" in report.evidence and "1.5.0" in report.evidence


def test_classify_regressed_when_version_anchor_not_behind() -> None:
    """The version-anchor STALE path only fires when the goal is actually
    behind. A goal anchored at the live version with an absent success signal
    is a REGRESSED, not STALE (no benign explanation)."""
    kf = _believed_kf("login", anchor_version="1.5.0")
    result = _run_result("login", RegressionVerdict.UNCERTAIN, matched_success=[])
    report = classify_goal(kf, result, current_version="1.5.0")
    assert report.verdict == AggregateVerdict.REGRESSED


def _multi_version_kf(goal_id: str, versions: list[str]) -> KnowledgeFile:
    """A believed goal whose success signals carry DIFFERENT
    `observed_app_version`s, one signal per version. Used to exercise the
    goal-anchor selection across the 9-vs-10 minor boundary, where raw-string
    `min` would pick the newer (lexicographically-smaller) "1.10.0" over the
    semver-oldest "1.9.0"."""
    signals = [
        Signal(
            type=SignalType.BEHAVIORAL,
            value=f"signal observed at app version {v}",
            provenance=Provenance(
                source_type=SourceType.HUMAN,
                source_id="pablo",
                observed_app_version=v,
                last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
                observation_count=1,
            ),
            confidence=1.0,
            status=Status.BELIEVED,
        )
        for v in versions
    ]
    return KnowledgeFile(
        schema_version="0",
        goal_id=goal_id,
        goal=f"goal {goal_id}",
        target=Target(app="testapp"),
        success_signals=signals,
        failure_signals=[],
        meta=Meta(created_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
                  updated_at=datetime(2026, 6, 7, tzinfo=timezone.utc)),
    )


def test_goal_version_anchor_is_semver_oldest_not_lexicographic() -> None:
    """The goal anchor is the SEMVER-oldest believed success version, the one
    that decays first against the live app (ADR-0013). Across the 9-vs-10 minor
    boundary, raw-string `min(["1.9.0", "1.10.0"])` returns "1.10.0" (lexico-
    graphic: "1.1" < "1.9"), the NEWER anchor; the correct semver-oldest is
    "1.9.0". Pinning this catches a regression to string ordering."""
    kf = _multi_version_kf("login", ["1.9.0", "1.10.0"])
    assert _goal_version_anchor(kf) == "1.9.0"
    # Order-independent: the same anchor regardless of signal declaration order.
    kf_rev = _multi_version_kf("login", ["1.10.0", "1.9.0"])
    assert _goal_version_anchor(kf_rev) == "1.9.0"


def test_version_anchor_stale_routing_uses_semver_oldest_across_9_10_boundary() -> None:
    """The semver-correct anchor routes a genuine drift to STALE where the
    lexicographic anchor would have under-fired and misrouted it to REGRESSED.

    A goal carries believed success at "1.9.0" and "1.10.0"; the live app is
    "1.12.0". The semver-oldest anchor "1.9.0" is 3 minors back (> default N=2),
    so the absent-success UNCERTAIN run is correctly STALE (the knowledge is
    pinned to an older app version, re-seed). The buggy lexicographic anchor
    "1.10.0" is only 2 minors back (not > N=2), so the version-anchor STALE path
    would NOT fire and the goal would be misrouted to REGRESSED."""
    kf = _multi_version_kf("login", ["1.9.0", "1.10.0"])
    result = _run_result("login", RegressionVerdict.UNCERTAIN, matched_success=[])
    report = classify_goal(kf, result, current_version="1.12.0")
    assert report.verdict == AggregateVerdict.STALE
    assert not report.fails_run
    # The named anchor is the semver-oldest "1.9.0", not the lexicographic
    # "1.10.0"; and the live version is named.
    assert "1.9.0" in report.evidence
    assert "1.12.0" in report.evidence


# --- ADR-0026 decision 5: AUTH-EXPIRED verdict + classification ------------


def test_classify_auth_expired_when_authed_scope_expected_and_logged_out() -> None:
    """A goal that expects an authenticated scope (auth_state authenticated +
    non-anonymous role) but reports authenticated=False (a login wall) is
    AUTH-EXPIRED, NOT FAIL/REGRESSED and NOT STALE. The saved session expired;
    the app did not break and the knowledge is not stale (ADR-0026 decision
    5). The expired role is named, and it is a loud non-OK outcome that fails
    the run."""
    kf = _authed_kf("dashboard", scope="admin")
    # The literal success signal is absent (logged out, so the post-login
    # control never appears): without the auth route this would look like an
    # absent-success REGRESSED. The auth route wins.
    result = _run_result(
        "dashboard", RegressionVerdict.UNCERTAIN,
        matched_success=[], authenticated=False,
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.AUTH_EXPIRED
    assert report.verdict != AggregateVerdict.REGRESSED
    assert report.verdict != AggregateVerdict.STALE
    assert not report.verdict.is_ok
    # Loud non-OK: fails the run, names the expired role.
    assert report.fails_run
    assert "admin" in report.signals
    assert "admin" in report.evidence
    assert "could not authenticate" in report.evidence


def test_classify_auth_expired_routes_ahead_of_fired_failure_signal() -> None:
    """AUTH-EXPIRED is decided BEFORE FAIL/REGRESSED: a logged-out run for an
    authenticated-scope goal is AUTH-EXPIRED even if a failure signal also
    fired, because the run could not authenticate in the first place (ADR-0026
    decision 5: routed ahead of the break verdict)."""
    kf = _authed_kf("dashboard", scope="user")
    result = _run_result(
        "dashboard", RegressionVerdict.FAIL,
        matched_failure=["an invalid credentials banner appears"],
        authenticated=False,
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.AUTH_EXPIRED


def test_authenticated_run_with_fired_failure_signal_is_still_regressed() -> None:
    """An AUTHENTICATED run (the session is valid) whose failure signal fired is
    a real REGRESSED, not AUTH-EXPIRED: the run authenticated fine and the app
    broke. AUTH-EXPIRED only fires on authenticated=False."""
    kf = _authed_kf("dashboard", scope="user")
    result = _run_result(
        "dashboard", RegressionVerdict.FAIL,
        matched_failure=["an invalid credentials banner appears"],
        authenticated=True,
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.REGRESSED
    assert report.fails_run


def test_anonymous_scope_goal_is_never_auth_expired() -> None:
    """An anonymous-scope goal (no authenticated scope expected) is NEVER
    AUTH-EXPIRED, even when the run reports authenticated=False: an anonymous
    flow has no session to expire. An absent success signal with no benign
    explanation stays REGRESSED (the anonymous-scope path is unaffected by
    ADR-0026)."""
    kf = _anon_kf("public-page")
    result = _run_result(
        "public-page", RegressionVerdict.UNCERTAIN,
        matched_success=[], authenticated=False,
    )
    report = classify_goal(kf, result)
    assert report.verdict != AggregateVerdict.AUTH_EXPIRED
    assert report.verdict == AggregateVerdict.REGRESSED


def test_goal_with_no_auth_state_is_never_auth_expired() -> None:
    """A goal with no auth_state at all (the default for every existing seed) is
    never AUTH-EXPIRED, even with authenticated=False, so existing goals and
    tests are unaffected by the additive flag."""
    kf = _believed_kf("login")  # no auth_state
    result = _run_result("login", RegressionVerdict.UNCERTAIN,
                         matched_success=[], authenticated=False)
    report = classify_goal(kf, result)
    assert report.verdict != AggregateVerdict.AUTH_EXPIRED
    assert report.verdict == AggregateVerdict.REGRESSED


def test_authed_scope_goal_passes_when_authenticated() -> None:
    """An authenticated-scope goal whose run authenticated fine and observed all
    believed success signals is plain OK: the auth route only diverts a
    logged-out run, never a healthy authenticated one."""
    kf = _authed_kf("dashboard", scope="user")
    result = _run_result(
        "dashboard", RegressionVerdict.PASS,
        matched_success=["a Sign out control is present after submitting valid credentials"],
        authenticated=True,
    )
    report = classify_goal(kf, result)
    assert report.verdict == AggregateVerdict.OK
    assert not report.fails_run


def test_one_auth_expired_goal_is_a_loud_non_ok_that_fails_the_run() -> None:
    """One AUTH-EXPIRED goal among OK goals makes the aggregate a loud non-OK
    outcome that fails the run and names the expired role, never a silent green
    and never mislabeled REGRESSED (ADR-0026 decision 5, AGENTS.md
    loud-over-silent)."""
    ok = GoalReport(
        "public", AggregateVerdict.OK,
        "all 1 believed success signals observed",
    )
    expired = classify_goal(
        _authed_kf("dashboard", scope="admin"),
        _run_result("dashboard", RegressionVerdict.UNCERTAIN,
                    matched_success=[], authenticated=False),
    )
    reports = [ok, expired]
    # The single AUTH-EXPIRED fails the whole run.
    assert aggregate_run_failed(reports)
    # The aggregate markdown leads with the loud failure and names the role,
    # never green and never REGRESSED.
    md = to_aggregate_markdown(reports)
    assert "RUN FAILED" in md
    assert "AUTH-EXPIRED" in md
    assert "`dashboard`" in md
    assert "admin" in md
    assert "RUN PASSED" not in md
    # The expired goal is named AUTH-EXPIRED, never mislabeled REGRESSED: its
    # own verdict line carries the AUTH-EXPIRED token, not REGRESSED.
    dashboard_line = next(
        ln for ln in md.splitlines() if "`dashboard`" in ln and "AUTH-EXPIRED" in ln
    )
    assert "REGRESSED" not in dashboard_line
    # And no goal in this run is classified REGRESSED (n_reg == 0).
    assert expired.verdict == AggregateVerdict.AUTH_EXPIRED
    # A "mostly green" framing must never appear: one AUTH-EXPIRED is loud.
    assert "mostly green" not in md.lower()


def test_aggregate_report_routes_auth_expired_to_reauthenticate_distinctly() -> None:
    """The aggregate report routes an AUTH-EXPIRED goal to a re-authenticate /
    refresh message, DISTINCT from the REGRESSED "file a bug" routing and the
    STALE "re-seed / update the knowledge" routing (ADR-0026 decision 5). The
    three outcomes coexist in one report so the distinctness is asserted on the
    same render: each goal's row carries its own routing, never collapsed."""
    regressed = classify_goal(
        _believed_kf("login"),
        _run_result("login", RegressionVerdict.FAIL,
                    matched_failure=["an invalid credentials banner appears"]),
    )
    stale = classify_goal(
        _believed_kf("profile"),
        _run_result("profile", RegressionVerdict.UNCERTAIN,
                    matched_success=[], healthy_equivalent_observed=True),
    )
    expired = classify_goal(
        _authed_kf("dashboard", scope="admin"),
        _run_result("dashboard", RegressionVerdict.UNCERTAIN,
                    matched_success=[], authenticated=False),
    )
    assert regressed.verdict == AggregateVerdict.REGRESSED
    assert stale.verdict == AggregateVerdict.STALE
    assert expired.verdict == AggregateVerdict.AUTH_EXPIRED

    md = to_aggregate_markdown([regressed, stale, expired])

    # The AUTH-EXPIRED goal's row routes to re-authenticate / refresh, NOT to
    # "file a bug" (REGRESSED) and NOT to "re-seed / update the knowledge"
    # (STALE).
    dash_line = next(ln for ln in md.splitlines() if "`dashboard`" in ln)
    assert "re-authenticate" in dash_line
    assert "refresh the session" in dash_line
    assert "file a bug" not in dash_line
    assert "re-seed" not in dash_line

    # The other two outcomes keep their own, different routing on their own rows.
    login_line = next(ln for ln in md.splitlines() if "`login`" in ln)
    assert "file a bug" in login_line
    assert "re-authenticate" not in login_line
    profile_line = next(ln for ln in md.splitlines() if "`profile`" in ln)
    assert "re-seed" in profile_line
    assert "re-authenticate" not in profile_line


def test_aggregate_rollup_counts_auth_expired_distinctly_from_regressed_and_stale() -> None:
    """The roll-up / count line counts AUTH-EXPIRED goals DISTINCTLY from
    REGRESSED and STALE (ADR-0026 decision 5): an expired session is neither a
    broken app nor outdated knowledge, so it gets its own bucket. The
    RUN FAILED breakdown also names the AUTH-EXPIRED count separately so the
    action (re-authenticate) is not folded into the REGRESSED bug-filing
    bucket."""
    regressed = classify_goal(
        _believed_kf("login"),
        _run_result("login", RegressionVerdict.FAIL,
                    matched_failure=["an invalid credentials banner appears"]),
    )
    stale = classify_goal(
        _believed_kf("profile"),
        _run_result("profile", RegressionVerdict.UNCERTAIN,
                    matched_success=[], healthy_equivalent_observed=True),
    )
    expired = classify_goal(
        _authed_kf("dashboard", scope="admin"),
        _run_result("dashboard", RegressionVerdict.UNCERTAIN,
                    matched_success=[], authenticated=False),
    )
    md = to_aggregate_markdown([regressed, stale, expired])

    # The single roll-up line counts each outcome in its own slot: exactly one
    # AUTH-EXPIRED, one REGRESSED, one STALE.
    rollup = next(
        ln for ln in md.splitlines() if "AUTH-EXPIRED" in ln and "REGRESSED" in ln
        and "STALE" in ln and "goal(s)" in ln
    )
    assert "1 REGRESSED" in rollup
    assert "1 STALE" in rollup
    assert "1 AUTH-EXPIRED" in rollup
    # The RUN FAILED breakdown names AUTH-EXPIRED as its own count, not merged
    # into the REGRESSED tally.
    failed_line = next(ln for ln in md.splitlines() if "RUN FAILED" in ln)
    assert "1 AUTH-EXPIRED" in failed_line
    assert "1 REGRESSED" in failed_line
    # The expired goal's named expired role appears in the failure summary.
    assert "admin" in md


# --- ADR-0026 decision 5: CLI _cmd_regress exit code on AUTH-EXPIRED -------


def test_cli_regress_exits_non_zero_when_an_auth_expired_goal_is_present(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """The console `praxis regress` (default-all aggregate) exits NON-ZERO when
    any goal is AUTH-EXPIRED, exactly like REGRESSED / ERROR (ADR-0026 decision
    5: an expired session fails the run loudly, never a silent green and never a
    false REGRESSED). The exit branch lives in `_cmd_regress` and computes
    failure from `aggregate_run_failed`, which includes AUTH-EXPIRED in the
    fails-run set.

    The library-half projection does not yet pass `auth_state` through to the
    aggregate read path (that seam is Step 5), so we inject the AUTH-EXPIRED
    GoalReport at the engine seam `_cmd_regress` calls. This exercises the CLI
    exit-code contract and the written report end to end without depending on a
    later step's wiring."""
    import sys

    import praxis.cli.main  # noqa: F401 - ensure the submodule is imported
    # `praxis.cli.__init__` re-exports the `main` FUNCTION as `praxis.cli.main`,
    # shadowing the submodule attribute; reach the real module via sys.modules so
    # the monkeypatch targets the name `_cmd_regress` actually calls.
    cli_main = sys.modules["praxis.cli.main"]

    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["dashboard"])

    expired = classify_goal(
        _authed_kf("dashboard", scope="admin"),
        _run_result("dashboard", RegressionVerdict.UNCERTAIN,
                    matched_success=[], authenticated=False),
    )
    assert expired.verdict == AggregateVerdict.AUTH_EXPIRED

    def fake_engine(*_args: Any, **_kwargs: Any) -> list[GoalReport]:
        return [expired]

    monkeypatch.setattr(cli_main, "regress_aggregate_engine", fake_engine)

    old = Path.cwd()
    os.chdir(root)
    try:
        rc = cli_run(["regress"])
    finally:
        os.chdir(old)

    # AUTH-EXPIRED fails the run loudly: non-zero exit, like REGRESSED / ERROR.
    assert rc == 1
    # The written aggregate report names AUTH-EXPIRED and the expired role, and
    # never reports the run as passed.
    md_files = sorted(
        (root / ".praxis" / "runs").glob("*/regress-aggregate.md")
    )
    assert md_files
    report_text = md_files[-1].read_text()
    assert "AUTH-EXPIRED" in report_text
    assert "`dashboard`" in report_text
    assert "admin" in report_text
    assert "RUN FAILED" in report_text
    assert "RUN PASSED" not in report_text


def test_cli_regress_exit_code_unchanged_for_ok_run(tmp_path: Path) -> None:
    """The AUTH-EXPIRED exit wiring does not regress the existing exit contract:
    an all-OK aggregate run still exits 0 (the fails-run set is unchanged for
    OK / STALE; only REGRESSED / ERROR / AUTH-EXPIRED fail)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["a"])
    obs_file = root / "pass.json"
    obs_file.write_text(json.dumps(_PASS_OBS))
    old = Path.cwd()
    os.chdir(root)
    try:
        rc = cli_run(["regress", "--from-file", str(obs_file)])
    finally:
        os.chdir(old)
    assert rc == 0


# --- decision 2 + 4: default-all aggregate, loud single regression ---------


def test_aggregate_runs_every_goal_and_reports_each(tmp_path: Path) -> None:
    """No `--goal`: the aggregate runs EVERY believed goal and emits one report
    line per goal (decision 2). Here all three pass -> OK across the board."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["a", "b", "c"])
    reports = regress_aggregate_via_skill(_const_brain(_PASS_OBS), project_start=root)
    assert {r.goal_id for r in reports} == {"a", "b", "c"}
    assert all(r.verdict == AggregateVerdict.OK for r in reports)
    assert not aggregate_run_failed(reports)


def test_single_regressed_goal_fails_run_and_names_signal(tmp_path: Path) -> None:
    """One REGRESSED goal among many OK goals fails the whole run loudly: the
    aggregate exits non-zero, the regressed goal is named, and the signal that
    flipped is named (decision 4). It never hides behind a 'mostly green'
    roll-up.

    Both surfaces are exercised: the console (`praxis regress` no `--goal`,
    process exit code) and the direct-call skill (GoalReport list).
    """
    # The brain passes every goal EXCEPT `b`, which trips its failure signal.
    def mixed_brain(prompt: str) -> dict[str, Any]:
        if "GOAL (b)" in prompt:
            return json.loads(json.dumps(_FAIL_OBS))
        return json.loads(json.dumps(_PASS_OBS))

    # Console surface: write the brain output per goal to a single file is not
    # possible (one --from-file is one fixed output), so drive the console with
    # the all-fail-on-b brain via the skill surface for the report, and assert
    # the console exit code on a from-file fail run below. Here we use the skill
    # surface to prove the named-signal contract, then the console exit code.
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    _init_with_goals(skill_root, ["a", "b", "c"])
    reports = regress_aggregate_via_skill(mixed_brain, project_start=skill_root)

    by_goal = {r.goal_id: r for r in reports}
    assert by_goal["a"].verdict == AggregateVerdict.OK
    assert by_goal["c"].verdict == AggregateVerdict.OK
    assert by_goal["b"].verdict == AggregateVerdict.REGRESSED
    # The single REGRESSED fails the run; the roll-up does not bury it.
    assert aggregate_run_failed(reports)
    # The named goal + named signal are recoverable.
    assert "an invalid credentials banner appears" in by_goal["b"].signals

    # The aggregate markdown leads with the loud failure and names the signal,
    # never reports 'mostly green' for 2/3 OK.
    md = to_aggregate_markdown(reports)
    assert "RUN FAILED" in md
    assert "`b`" in md and "REGRESSED" in md
    assert "an invalid credentials banner appears" in md
    assert "mostly green" not in md.lower()

    # Console surface exit code: a from-file fail run on the aggregate path
    # exits non-zero (the brain returns the same FAIL output for every goal).
    console_root = tmp_path / "console"
    console_root.mkdir()
    _init_with_goals(console_root, ["a", "b"])
    obs_file = console_root / "fail.json"
    obs_file.write_text(json.dumps(_FAIL_OBS))
    old = Path.cwd()
    os.chdir(console_root)
    try:
        rc = cli_run(["regress", "--from-file", str(obs_file)])
    finally:
        os.chdir(old)
    assert rc == 1  # REGRESSED -> loud non-zero
    # The aggregate report landed under runs/<timestamp>/.
    md_files = sorted((console_root / ".praxis" / "runs").glob("*/regress-aggregate.md"))
    assert md_files
    report_text = md_files[-1].read_text()
    assert "RUN FAILED" in report_text


def test_stale_goal_alone_does_not_fail_the_run(tmp_path: Path) -> None:
    """A STALE goal (drift) does NOT fail the run: the app changed on purpose,
    the fix is a human re-seed, not a red gate (decision 4 + the consequences
    note). The console aggregate exits 0 with only STALE goals."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["a"])
    obs_file = root / "stale.json"
    obs_file.write_text(json.dumps(_HEALTHY_EQUIVALENT_OBS))
    old = Path.cwd()
    os.chdir(root)
    try:
        rc = cli_run(["regress", "--from-file", str(obs_file)])
    finally:
        os.chdir(old)
    assert rc == 0  # STALE alone is not a regression
    reports = regress_aggregate_via_skill(
        _const_brain(_HEALTHY_EQUIVALENT_OBS), project_start=root,
    )
    assert reports[0].verdict == AggregateVerdict.STALE
    assert not aggregate_run_failed(reports)


# --- decision 4: an errored goal is non-OK and fails the run ---------------


def test_errored_goal_is_non_ok_and_fails_the_run(tmp_path: Path) -> None:
    """A goal whose run throws (the adapter cannot reach a verdict) is surfaced
    as a loud ERROR that fails the run; it is never silently skipped and never
    counted OK (decision 4 + forbidden alternative)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["a", "b"])
    proj = discover_project(root)
    adapter = proj.adapter()
    runner = RegressionRunner(adapter, agent_id=proj.agent_id)

    # A brain that raises on goal `b` only: `b` cannot reach a verdict.
    def exploding_brain(prompt: str) -> dict[str, Any]:
        if "GOAL (b)" in prompt:
            raise RuntimeError("adapter could not load the app")
        return json.loads(json.dumps(_PASS_OBS))

    reports = run_aggregate(runner, ["a", "b"], exploding_brain)
    by_goal = {r.goal_id: r for r in reports}
    # `a` still reached a verdict (not dropped because `b` blew up).
    assert by_goal["a"].verdict == AggregateVerdict.OK
    # `b` is a loud ERROR, non-OK, and fails the run.
    assert by_goal["b"].verdict == AggregateVerdict.ERROR
    assert not by_goal["b"].verdict.is_ok
    assert by_goal["b"].fails_run
    assert "could not reach a verdict" in by_goal["b"].evidence
    assert aggregate_run_failed(reports)
    # Every goal is present: the errored goal is never silently skipped.
    assert {r.goal_id for r in reports} == {"a", "b"}


def test_errored_goal_is_not_counted_ok_in_rollup(tmp_path: Path) -> None:
    """The roll-up never counts an ERROR goal as OK: the aggregate markdown
    shows the ERROR count and leads with RUN FAILED."""
    a = GoalReport("a", AggregateVerdict.OK, "ok")
    b = GoalReport("b", AggregateVerdict.ERROR, "could not reach a verdict: boom")
    md = to_aggregate_markdown([a, b])
    assert "1 OK" in md and "1 ERROR" in md
    assert "RUN FAILED" in md
    assert aggregate_run_failed([a, b])


# --- decision 7: per-goal budget slice; exhaustion is a loud ERROR ---------


def test_budget_exhaustion_is_a_loud_error_not_a_silent_skip(tmp_path: Path) -> None:
    """A goal that exhausts its per-goal token slice is a loud ERROR for that
    goal (decision 7), not a silent skip and not a trusted verdict. The other
    goals, within their own slices, still reach their verdicts: the slice is
    per-goal, not one shared pool."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["cheap", "expensive"])
    proj = discover_project(root)
    runner = RegressionRunner(proj.adapter(), agent_id=proj.agent_id)

    # `expensive` burns 10_000 tokens; `cheap` uses 100. With a 1_000-token
    # per-goal slice, `expensive` exhausts its slice and `cheap` does not.
    def metered_brain(prompt: str) -> dict[str, Any]:
        obs = json.loads(json.dumps(_PASS_OBS))
        obs["tokens"] = 10_000 if "GOAL (expensive)" in prompt else 100
        return obs

    reports = run_aggregate(
        runner, ["cheap", "expensive"], metered_brain,
        budget_tokens_per_goal=1_000,
    )
    by_goal = {r.goal_id: r for r in reports}
    # The cheap goal stays within its OWN slice and reaches its verdict.
    assert by_goal["cheap"].verdict == AggregateVerdict.OK
    # The expensive goal exhausted its slice: a loud, named ERROR.
    assert by_goal["expensive"].verdict == AggregateVerdict.ERROR
    assert "budget exhausted" in by_goal["expensive"].evidence
    assert "10000" in by_goal["expensive"].evidence
    assert aggregate_run_failed(reports)


def test_wall_time_budget_exhaustion_is_a_loud_error(tmp_path: Path) -> None:
    """A goal that exceeds its per-goal wall-time slice is a loud ERROR. The
    executor is opaque, so the slice is enforced as a post-hoc cap on the
    observed wall time."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["slow"])
    proj = discover_project(root)
    runner = RegressionRunner(proj.adapter(), agent_id=proj.agent_id)

    import time as _time

    def slow_brain(prompt: str) -> dict[str, Any]:
        _time.sleep(0.05)  # exceeds the 0.001s wall slice below
        return json.loads(json.dumps(_PASS_OBS))

    reports = run_aggregate(
        runner, ["slow"], slow_brain, budget_wall_seconds_per_goal=0.001,
    )
    assert reports[0].verdict == AggregateVerdict.ERROR
    assert "wall" in reports[0].evidence.lower()
    assert aggregate_run_failed(reports)


def test_per_goal_budget_is_not_a_shared_pool(tmp_path: Path) -> None:
    """The budget is sliced per goal, not raced for: two goals each at 800
    tokens both pass under an 1_000-token-PER-GOAL slice, even though their sum
    (1_600) would blow a single shared 1_000 pool."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["a", "b"])
    proj = discover_project(root)
    runner = RegressionRunner(proj.adapter(), agent_id=proj.agent_id)

    def brain(prompt: str) -> dict[str, Any]:
        obs = json.loads(json.dumps(_PASS_OBS))
        obs["tokens"] = 800
        return obs

    reports = run_aggregate(runner, ["a", "b"], brain, budget_tokens_per_goal=1_000)
    assert all(r.verdict == AggregateVerdict.OK for r in reports)
    assert not aggregate_run_failed(reports)


# --- decision 6: auditor scenarios are NOT an input ------------------------


def test_auditor_scenarios_are_not_an_input(tmp_path: Path) -> None:
    """R-mode keeps the ADR-0009 leak closed: the aggregate engine takes only
    the believed knowledge + the brain output. There is no parameter, and no
    code path, by which auditor scenarios enter the regress operation
    (decision 6 + the forbidden alternative).

    We assert it structurally: the aggregate entry points accept no auditor /
    scenario / answer-key argument, and a run completes reading only the
    store's believed signals. If a future change added an auditor input it
    would have to change these signatures, which this test pins.
    """
    import inspect

    from praxis.runner import regress_aggregate_engine

    for fn in (regress_aggregate_engine, run_aggregate, regress_aggregate_via_skill):
        params = set(inspect.signature(fn).parameters)
        for banned in ("auditor", "scenarios", "scenario", "answer_key",
                       "ground_truth", "oracle_scenarios"):
            assert banned not in params, (
                f"{fn.__name__} exposes an auditor-shaped input {banned!r}; "
                f"R-mode must read believed signals only (ADR-0009 / ADR-0023 "
                f"decision 6)"
            )

    # And a real aggregate run reaches its verdicts with no auditor data fed in:
    # the only inputs are the seeded believed signals and the brain's
    # observations.
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["a"])
    reports = regress_aggregate_via_skill(_const_brain(_PASS_OBS), project_start=root)
    assert reports[0].verdict == AggregateVerdict.OK
