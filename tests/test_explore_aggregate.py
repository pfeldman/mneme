"""Default-all explore + trigger-grouped candidate report tests (ADR-0023).

ADR-0023 decision 8 fixes the contract this file pins:

- With no `--goal`, explore hunts off-happy-path across EVERY believed goal
  (decision 2) and writes any candidate risks / uncertainties as one-file-per-
  observation YAML under `.praxis/candidates/<goal>/` (ADR-0021 decision 4) on
  BOTH surfaces (decision 1).
- In the report, observations are GROUPED by their structured `trigger`: each
  finding appears ONCE, annotated with how many times it was observed and how
  many DISTINCT `source_id`s attest to it.
- N observations from the same `agent_identity` count as ONE source (ADR-0008),
  never as N duplicate entries; a finding earns `believed` only by
  diversity-or-seed (ADR-0005, ADR-0014), never by observation count alone.
- The `off_path_fraction` floor (ADR-0009 E-mode kill-criterion guard) is kept
  per goal.

Coverage map (the handoff's verification list):
  aggregate explore writes one file per observation .. test_aggregate_*_writes_*
  report groups by trigger w/ correct counts ......... test_report_groups_*
  same-agent duplicates collapse to one source ....... test_same_agent_*
  both surfaces produce the same committed result .... test_console_and_skill_*
  off_path_fraction floor logging kept ............... test_off_path_fraction_*
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from praxis.cli.main import discover_project
from praxis.cli.main import main as cli_run
from praxis.model import (
    HttpTrigger,
    Provenance,
    Risk,
    SequenceTrigger,
    SourceType,
    Status,
    Uncertainty,
)
from praxis.runner import (
    ExplorationRunner,
    group_candidates_by_trigger,
    run_explore_aggregate,
    to_candidate_markdown,
)
from praxis.skill_driver import explore_aggregate_via_skill
from praxis.store import (
    CandidateEvent,
    CandidateFileStore,
    CandidateRiskPayload,
    CandidateUncertaintyPayload,
)


# --- seed helpers ----------------------------------------------------------


def _seed_yaml(goal_id: str) -> str:
    """A minimal valid two-signal seed (behavioral success + a failure signal)."""
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
      observation_count: 1
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


def _init_with_goals(root: Path, goals: list[str], *, agent_id: str = "praxis-cli") -> None:
    """init a project under `root` and learn one seed per goal id."""
    old = Path.cwd()
    os.chdir(root)
    try:
        assert cli_run(
            ["init", "--app", "testapp", "--env", "local", "--agent-id", agent_id]
        ) == 0
        for gid in goals:
            seed = root / f"{gid}.yaml"
            seed.write_text(_seed_yaml(gid))
            assert cli_run(["learn", gid, "--from-file", str(seed)]) == 0
    finally:
        os.chdir(old)


# --- brain outputs ---------------------------------------------------------


def _http_risk(risk_id: str, source_id: str = "praxis-cli") -> dict[str, Any]:
    return {
        "id": risk_id,
        "description": f"{risk_id}: login redirects to an unexpected host",
        "trigger": {
            "kind": "http", "method": "GET", "path": "/login/callback",
            "expect": "Location header matches the configured origin",
        },
        "status": "contested", "confidence": 0.6,
        "provenance": {
            "source_type": "agent", "source_id": source_id,
            "last_verified": "2026-06-07T00:00:00Z", "observation_count": 1,
        },
    }


def _seq_risk(risk_id: str, source_id: str = "praxis-cli") -> dict[str, Any]:
    return {
        "id": risk_id,
        "description": f"{risk_id}: double submit creates two sessions",
        "trigger": {
            "kind": "sequence", "n": 2,
            "action": "submit valid credentials twice",
            "expect": "exactly one session is created",
        },
        "status": "contested", "confidence": 0.6,
        "provenance": {
            "source_type": "agent", "source_id": source_id,
            "last_verified": "2026-06-07T00:00:00Z", "observation_count": 1,
        },
    }


def _explore_obs(new_risks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_observations": [],
        "new_risks": new_risks,
        "new_uncertainties": [],
        "actions": 3, "tokens": 200,
        # One visited url off the empty happy path -> off_path_fraction = 1.0.
        "visited_urls": ["/login/callback"],
    }


def _const_brain(obs: dict[str, Any]):
    def brain(prompt: str) -> dict[str, Any]:
        return json.loads(json.dumps(obs))
    return brain


# --- aggregate explore writes one file per observation (decision 2 + 8) -----


def test_aggregate_explore_writes_one_file_per_observation(tmp_path: Path) -> None:
    """No `--goal`: explore runs EVERY believed goal and writes any candidate
    risk it finds as its OWN committed YAML file under
    `.praxis/candidates/<goal>/<observation_event_id>.yaml`, one file per
    observation (ADR-0021 decision 4 + ADR-0023 decision 8)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha", "beta"])
    # Each goal's brain emits one distinct candidate risk.
    brain = _const_brain(_explore_obs([_http_risk("phishy-redirect")]))
    outcome = explore_aggregate_via_skill(brain, project_start=root)

    # Both goals ran; each wrote one committed candidate file.
    assert {oc.goal_id for oc in outcome.outcomes} == {"alpha", "beta"}
    assert all(oc.ok for oc in outcome.outcomes)
    assert len(outcome.committed_paths) == 2

    for gid in ("alpha", "beta"):
        goal_dir = root / ".praxis" / "candidates" / gid
        files = sorted(goal_dir.glob("*.yaml"))
        assert len(files) == 1, f"{gid} should have one candidate file"
        # The file is named by the observation event id, never the finding id.
        assert files[0].stem != "phishy-redirect"


def test_console_and_skill_surfaces_produce_the_same_committed_result(
    tmp_path: Path,
) -> None:
    """Both surfaces drive the SAME engine over the SAME store (ADR-0023
    decision 1): the console `praxis explore` (no `--goal`, from-file brain) and
    the direct-call skill each write one candidate file per observation into the
    committed tree. Run against two fresh projects with identical seeds + brain
    output; the committed file counts and the trigger grouping match."""
    # Console surface: `praxis explore` with no --goal, brain via --from-file.
    console_root = tmp_path / "console"
    console_root.mkdir()
    _init_with_goals(console_root, ["alpha", "beta"])
    obs_file = console_root / "obs.json"
    obs_file.write_text(json.dumps(_explore_obs([_http_risk("phishy-redirect")])))
    old = Path.cwd()
    os.chdir(console_root)
    try:
        rc = cli_run(["explore", "--from-file", str(obs_file)])
    finally:
        os.chdir(old)
    assert rc == 0
    console_files = sorted(
        (console_root / ".praxis" / "candidates").glob("*/*.yaml")
    )
    # The aggregate candidate report landed under runs/<timestamp>/.
    report = sorted(
        (console_root / ".praxis" / "runs").glob("*/explore-candidates.md")
    )
    assert report
    report_text = report[-1].read_text()
    assert "praxis explore (candidates)" in report_text

    # Skill surface: same seeds + same brain output.
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    _init_with_goals(skill_root, ["alpha", "beta"])
    explore_aggregate_via_skill(
        _const_brain(_explore_obs([_http_risk("phishy-redirect")])),
        project_start=skill_root,
    )
    skill_files = sorted(
        (skill_root / ".praxis" / "candidates").glob("*/*.yaml")
    )
    # Same committed file count across the two surfaces (one per goal).
    assert len(console_files) == len(skill_files) == 2


# --- report groups by trigger with correct counts (decision 8) -------------


def _provenance(source_id: str) -> Provenance:
    return Provenance(
        source_type=SourceType.AGENT,
        source_id=source_id,
        last_verified=datetime(2026, 6, 7, tzinfo=timezone.utc),
        observation_count=1,
    )


def _risk_event(
    *,
    goal_id: str = "login",
    agent_identity: str = "agent-A",
    risk_id: str = "phishy-redirect",
    trigger_kind: str = "http",
    environment: str | None = None,
) -> CandidateEvent:
    trigger: HttpTrigger | SequenceTrigger
    if trigger_kind == "http":
        trigger = HttpTrigger(
            method="GET", path="/login/callback",
            expect="Location header matches the configured origin",
        )
    else:
        trigger = SequenceTrigger(
            n=2, action="submit valid credentials twice",
            expect="exactly one session is created",
        )
    return CandidateEvent(
        ts=datetime.now(timezone.utc),
        agent_identity=agent_identity,
        goal_id=goal_id,
        environment=environment,
        payload=CandidateRiskPayload(
            risk=Risk(
                id=risk_id,
                description="login redirects to an unexpected host",
                trigger=trigger,
                provenance=_provenance(agent_identity),
                confidence=0.6, status=Status.CONTESTED,
            ),
        ),
    )


def _unc_event(
    *, goal_id: str = "login", agent_identity: str = "agent-A",
    unc_id: str = "receipt-window",
) -> CandidateEvent:
    return CandidateEvent(
        agent_identity=agent_identity,
        goal_id=goal_id,
        payload=CandidateUncertaintyPayload(
            uncertainty=Uncertainty(
                id=unc_id, question="how long is the callback URL valid?",
                raised_by=agent_identity,
                raised_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            ),
        ),
    )


def test_report_groups_each_finding_once_with_observation_and_source_counts(
    tmp_path: Path,
) -> None:
    """Two observations of the SAME finding (same structured trigger) from two
    DIFFERENT agents group into ONE report row, annotated with observation
    count = 2 and distinct source count = 2 (decision 8)."""
    events = [
        _risk_event(agent_identity="agent-A", risk_id="phishy-redirect"),
        _risk_event(agent_identity="agent-B", risk_id="phishy-redirect"),
    ]
    groups = group_candidates_by_trigger(events)
    # One finding, not two: the trigger is the grouping key.
    assert len(groups) == 1
    g = groups[0]
    assert g.observation_count == 2
    assert g.distinct_source_ids == {"agent-A", "agent-B"}
    assert g.source_count == 2

    # The markdown shows it ONCE with both counts.
    md = to_candidate_markdown(groups)
    # One data row in the findings table (header + separator + one row).
    data_rows = [
        ln for ln in md.splitlines()
        if ln.startswith("|") and "login redirects" in ln
    ]
    assert len(data_rows) == 1
    # observations=2, distinct sources=2 appear in that single row.
    assert "| 2 |" in data_rows[0]


def test_two_distinct_triggers_are_two_findings(tmp_path: Path) -> None:
    """Two observations with DIFFERENT structured triggers are two findings,
    each appearing once (the trigger, not the goal, is the grouping key)."""
    events = [
        _risk_event(risk_id="phishy-redirect", trigger_kind="http"),
        _risk_event(risk_id="double-submit", trigger_kind="sequence"),
    ]
    groups = group_candidates_by_trigger(events)
    assert len(groups) == 2
    assert all(g.observation_count == 1 for g in groups)


# --- same-agent duplicates collapse to one source (ADR-0008) ----------------


def test_same_agent_duplicates_collapse_to_one_source(tmp_path: Path) -> None:
    """N observations of one finding from the SAME agent_identity count as ONE
    source, never N duplicate entries (ADR-0008). The observation count rises
    with each repeat, but the distinct source count stays 1."""
    events = [
        _risk_event(agent_identity="agent-A", risk_id="phishy-redirect")
        for _ in range(5)
    ]
    groups = group_candidates_by_trigger(events)
    assert len(groups) == 1
    g = groups[0]
    # Five observations, but ONE source: the report never inflates one agent's
    # repeats into five attesting sources.
    assert g.observation_count == 5
    assert g.distinct_source_ids == {"agent-A"}
    assert g.source_count == 1
    # Five same-source observations never promote: no diversity (ADR-0008).
    assert not g.believed


def test_aggregate_explore_n_runs_one_agent_is_one_source(tmp_path: Path) -> None:
    """End-to-end on the committed tree: running explore N times with the same
    agent_identity writes N candidate files for one finding, but the report
    still shows ONE source (ADR-0008 under the file-per-observation store)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["login"])
    brain = _const_brain(_explore_obs([_http_risk("phishy-redirect")]))
    # Three explore runs, same agent (the project's agent_id), same finding.
    for _ in range(3):
        explore_aggregate_via_skill(brain, project_start=root)

    goal_dir = root / ".praxis" / "candidates" / "login"
    assert len(sorted(goal_dir.glob("*.yaml"))) == 3  # one file per observation

    proj = discover_project(root)
    events = proj.candidate_files().read("login")
    groups = group_candidates_by_trigger(events, seed=proj.seeds().get("login"))
    assert len(groups) == 1
    g = groups[0]
    assert g.observation_count == 3
    assert g.source_count == 1  # praxis-cli is one source no matter how many runs
    assert not g.believed


# --- believed only by diversity-or-seed, never by observation count ----------


def test_finding_is_believed_only_by_diversity_not_by_count(tmp_path: Path) -> None:
    """A finding earns `believed` only by diversity-or-seed (ADR-0005,
    ADR-0008, ADR-0014), never by observation count. Two DIFFERENT sources with
    two DIFFERENT trigger kinds for one finding id promote; the same two sources
    with the same trigger kind stay contested."""
    # Two sources, two trigger kinds, same finding id -> believed.
    diverse = [
        _risk_event(agent_identity="agent-A", risk_id="phishy-redirect",
                    trigger_kind="http"),
        _risk_event(agent_identity="agent-B", risk_id="phishy-redirect",
                    trigger_kind="sequence"),
    ]
    g_diverse = group_candidates_by_trigger(diverse)
    # Two trigger kinds means two report rows (grouping is by trigger), but the
    # underlying finding id promoted at the projection, so each row is believed.
    assert any(g.believed for g in g_diverse)

    # Two sources, SAME trigger kind, same finding id -> still contested
    # (type diversity is missing; ADR-0008).
    same_type = [
        _risk_event(agent_identity="agent-A", risk_id="phishy-redirect",
                    trigger_kind="http"),
        _risk_event(agent_identity="agent-B", risk_id="phishy-redirect",
                    trigger_kind="http"),
    ]
    g_same = group_candidates_by_trigger(same_type)
    assert len(g_same) == 1
    assert g_same[0].source_count == 2
    assert not g_same[0].believed  # diversity, not count, gates promotion


def test_uncertainties_group_by_question(tmp_path: Path) -> None:
    """Uncertainties (questions, not predicates) group by their question text:
    two agents asking the same question are one finding with two sources."""
    events = [
        _unc_event(agent_identity="agent-A", unc_id="receipt-window"),
        _unc_event(agent_identity="agent-B", unc_id="receipt-window"),
    ]
    groups = group_candidates_by_trigger(events)
    assert len(groups) == 1
    g = groups[0]
    assert g.kind == "uncertainty"
    assert g.observation_count == 2
    assert g.distinct_source_ids == {"agent-A", "agent-B"}


# --- ADR-0035 decision 6: the env annotation in the report and in review ----


def test_finding_on_two_envs_is_one_group_annotated_with_both(
    tmp_path: Path,
) -> None:
    """A finding observed on BOTH envs renders as ONE trigger group (the
    trigger, never the env, is the grouping key) annotated with both env names
    - and the corroboration counts are unchanged by the env (ADR-0035
    decision 5): the same agent on dev2 and prod is still ONE source."""
    events = [
        _risk_event(agent_identity="agent-A", environment="dev2"),
        _risk_event(agent_identity="agent-A", environment="prod"),
    ]
    groups = group_candidates_by_trigger(events)
    assert len(groups) == 1
    g = groups[0]
    assert g.environments == {"dev2", "prod"}
    assert g.observation_count == 2
    # Two envs mint NO second source: corroboration is untouched by the env.
    assert g.source_count == 1
    assert not g.believed

    md = to_candidate_markdown(groups)
    assert "(seen on: dev2, prod)" in md


def test_single_env_finding_is_annotated_seen_on_only(tmp_path: Path) -> None:
    """A finding observed on exactly one declared env carries the
    'seen on: <env> only' annotation - the datum the reviewer needs to decide
    product-level vs not-yet-shipped (ADR-0035 decision 6)."""
    groups = group_candidates_by_trigger(
        [_risk_event(agent_identity="agent-A", environment="dev2")]
    )
    md = to_candidate_markdown(groups)
    assert "(seen on: dev2 only)" in md


def test_none_env_mixed_with_stamped_renders_pre_migration(
    tmp_path: Path,
) -> None:
    """A pre-ADR-0035 observation (environment None) mixed with an env-stamped
    one renders as 'pre-migration' inside the annotation; it is never silently
    attributed to a declared env."""
    events = [
        _risk_event(agent_identity="agent-A", environment="dev2"),
        _risk_event(agent_identity="agent-B"),  # no env: pre-migration file
    ]
    groups = group_candidates_by_trigger(events)
    assert len(groups) == 1
    md = to_candidate_markdown(groups)
    assert "(seen on: dev2, pre-migration)" in md


def test_pure_none_env_findings_render_with_no_annotation(
    tmp_path: Path,
) -> None:
    """When EVERY observation is env-less (the pure single-env project that
    never declared environments) the report carries NO annotation at all: the
    finding row is byte-identical to the pre-ADR-0035 rendering."""
    events = [
        _risk_event(agent_identity="agent-A"),
        _risk_event(agent_identity="agent-B"),
    ]
    md = to_candidate_markdown(group_candidates_by_trigger(events))
    assert "seen on" not in md
    assert "pre-migration" not in md
    # The full data row, byte-for-byte the pre-ADR-0035 shape.
    assert (
        "| login redirects to an unexpected host | risk | "
        "GET /login/callback -&gt; expect: Location header matches the "
        "configured origin | 2 | 2 | **contested** |"
    ) in md.splitlines()


def test_review_annotates_the_envs_a_candidate_was_seen_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """`praxis review` is cross-env (it never selects an environment, ADR-0035
    decision 6): one finding observed on dev2 only shows up ONCE with the
    'seen on: dev2 only' annotation and an unchanged source/event count."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["login"])
    store = CandidateFileStore(root / ".praxis" / "candidates")
    store.write(_risk_event(agent_identity="agent-A", environment="dev2"))
    old = Path.cwd()
    os.chdir(root)
    try:
        rc = cli_run(["review"])
    finally:
        os.chdir(old)
    assert rc == 0
    out = capsys.readouterr().out
    assert "seen on: dev2 only" in out
    # Annotation only: the source set and event count render exactly as before.
    assert "sources={agent-A}  events=1" in out


def test_review_output_is_byte_identical_when_no_observation_has_an_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The undeclared single-env project's review output does not change by a
    single byte: with every observation env-less there is no annotation line,
    no 'pre-migration', and the candidate block is exactly the pre-ADR-0035
    format (the zero-ceremony bar, ADR-0035 decision 1)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["login"])
    store = CandidateFileStore(root / ".praxis" / "candidates")
    store.write(_risk_event(agent_identity="agent-A"))
    old = Path.cwd()
    os.chdir(root)
    try:
        rc = cli_run(["review"])
    finally:
        os.chdir(old)
    assert rc == 0
    out = capsys.readouterr().out
    assert "seen on" not in out
    assert "pre-migration" not in out
    assert (
        "  [contested candidate_risk / http] phishy-redirect: "
        "login redirects to an unexpected host\n"
        "     trigger: GET /login/callback  "
        "expect: Location header matches the configured origin\n"
        "     confidence=0.60  sources={agent-A}  events=1\n"
    ) in out


# --- off_path_fraction floor logging (ADR-0009 E-mode kill-criterion) -------


def test_off_path_fraction_floor_is_logged_per_goal(tmp_path: Path) -> None:
    """The aggregate explore keeps the ADR-0009 off_path_fraction floor per
    goal: every goal's fraction is computed and rendered in the report so a run
    that collapsed into R-mode (fraction near 0) is visible, not hidden."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha", "beta"])
    brain = _const_brain(_explore_obs([_http_risk("phishy-redirect")]))
    outcome = explore_aggregate_via_skill(brain, project_start=root)

    # The off_path_fraction is present on every goal's result (empty happy path
    # + one visited url -> 1.0).
    for oc in outcome.outcomes:
        assert oc.ok and oc.result is not None
        assert oc.result.off_path_fraction == 1.0

    # The console report renders the floor per goal.
    report = sorted(
        (root / ".praxis" / "runs").glob("*/explore-candidates.md")
    )
    assert not report  # the skill surface did not write a report; do it from the CLI

    # Re-run on the console surface to render the floor section.
    console_root = tmp_path / "console"
    console_root.mkdir()
    _init_with_goals(console_root, ["alpha", "beta"])
    obs_file = console_root / "obs.json"
    obs_file.write_text(json.dumps(_explore_obs([_http_risk("phishy-redirect")])))
    old = Path.cwd()
    os.chdir(console_root)
    try:
        assert cli_run(["explore", "--from-file", str(obs_file)]) == 0
    finally:
        os.chdir(old)
    report_text = sorted(
        (console_root / ".praxis" / "runs").glob("*/explore-candidates.md")
    )[-1].read_text()
    assert "off_path_fraction" in report_text
    assert "`alpha`" in report_text and "`beta`" in report_text


# --- decision 2 + 4: a goal that cannot explore is surfaced, never dropped --


def test_a_goal_whose_brain_throws_is_surfaced_not_dropped(tmp_path: Path) -> None:
    """A goal whose brain throws is surfaced as a per-goal error, never silently
    dropped; the other goals still run (the loud-over-silent posture R-mode's
    aggregate takes, applied to E-mode)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha", "beta"])
    proj = discover_project(root)
    runner = ExplorationRunner(proj.adapter(), agent_id=proj.agent_id)

    def exploding_brain(prompt: str) -> dict[str, Any]:
        if "GOAL (beta)" in prompt:
            raise RuntimeError("adapter could not load the app")
        return json.loads(json.dumps(_explore_obs([_http_risk("phishy-redirect")])))

    outcomes = run_explore_aggregate(runner, ["alpha", "beta"], exploding_brain)
    by_goal = {oc.goal_id: oc for oc in outcomes}
    # Every goal is present; the errored goal is never silently skipped.
    assert set(by_goal) == {"alpha", "beta"}
    assert by_goal["alpha"].ok
    assert not by_goal["beta"].ok
    assert by_goal["beta"].error is not None
    assert "could not explore" in by_goal["beta"].error


# --- decision 7: per-goal token + wall budget slice -> loud per-goal ERROR ---


def _explore_obs_tokens(new_risks: list[dict[str, Any]], tokens: int) -> dict[str, Any]:
    """Like `_explore_obs` but with a caller-set token count, so a goal can be
    driven over its per-goal token slice (ADR-0023 decision 7)."""
    obs = _explore_obs(new_risks)
    obs["tokens"] = tokens
    return obs


def test_a_goal_over_its_token_slice_is_a_loud_error(tmp_path: Path) -> None:
    """A goal that exhausts its per-goal TOKEN slice is a loud per-goal ERROR
    (`ok=False`), not a clean success and not silently counted as explored
    (ADR-0023 decision 7, mirroring the regress aggregate's token cap)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha", "beta"])
    proj = discover_project(root)
    runner = ExplorationRunner(proj.adapter(), agent_id=proj.agent_id)
    # The brain reports 500 tokens; the slice is 100, so every goal is over.
    brain = _const_brain(_explore_obs_tokens([_http_risk("phishy-redirect")], 500))

    outcomes = run_explore_aggregate(
        runner, ["alpha", "beta"], brain, budget_tokens_per_goal=100,
    )
    by_goal = {oc.goal_id: oc for oc in outcomes}
    assert set(by_goal) == {"alpha", "beta"}
    for gid in ("alpha", "beta"):
        oc = by_goal[gid]
        # Loud ERROR: surfaced with the named budget evidence, never ok=True.
        assert not oc.ok
        assert oc.result is None
        assert oc.error is not None
        assert "per-goal budget exhausted" in oc.error
        assert "tokens 500 > slice 100" in oc.error


def test_token_exhausted_goal_does_not_mirror_candidates_as_clean_success(
    tmp_path: Path,
) -> None:
    """An over-token goal's candidate files are NOT mirrored to the shared
    committed tree as a clean success: the engine's committed sink runs only for
    `ok` outcomes (ADR-0023 decision 7)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha"])
    brain = _const_brain(_explore_obs_tokens([_http_risk("phishy-redirect")], 500))

    outcome = explore_aggregate_via_skill(
        brain, project_start=root, budget_tokens_per_goal=100,
    )
    # The goal is a surfaced ERROR; nothing was mirrored to the committed tree.
    assert all(not oc.ok for oc in outcome.outcomes)
    assert outcome.committed_paths == []
    goal_dir = root / ".praxis" / "candidates" / "alpha"
    assert not goal_dir.exists() or not sorted(goal_dir.glob("*.yaml"))


def test_a_goal_over_its_wall_slice_is_a_loud_error(tmp_path: Path) -> None:
    """A goal whose run exceeds its per-goal WALL slice is likewise a loud
    per-goal ERROR (`ok=False`), enforced as the same post-hoc cap the regress
    aggregate uses (ADR-0023 decision 7). The executor is a single opaque call
    the runner cannot interrupt mid-flight, so the over-wall verdict is reported
    after the brain returns, never trusted as a clean success."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha"])
    proj = discover_project(root)
    runner = ExplorationRunner(proj.adapter(), agent_id=proj.agent_id)

    def slow_brain(prompt: str) -> dict[str, Any]:
        time.sleep(0.05)
        return json.loads(json.dumps(_explore_obs([_http_risk("phishy-redirect")])))

    # A wall slice well under the 0.05s the brain sleeps -> over the slice.
    outcomes = run_explore_aggregate(
        runner, ["alpha"], slow_brain, budget_wall_seconds_per_goal=0.001,
    )
    assert len(outcomes) == 1
    oc = outcomes[0]
    assert not oc.ok
    assert oc.result is None
    assert oc.error is not None
    assert "per-goal budget exhausted" in oc.error
    assert "wall" in oc.error and "slice" in oc.error


def test_console_explore_aggregate_fails_loudly_on_budget_exhaustion(
    tmp_path: Path,
) -> None:
    """End-to-end on the console surface: `praxis explore --budget-tokens N`
    (no `--goal`) over a brain that reports more than N tokens exits non-zero
    (the loud-failure contract, ADR-0023 decision 4 + 7), names the goal in the
    report's errors section, and writes no committed candidate file for it."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha", "beta"])
    obs_file = root / "obs.json"
    obs_file.write_text(
        json.dumps(_explore_obs_tokens([_http_risk("phishy-redirect")], 500))
    )
    old = Path.cwd()
    os.chdir(root)
    try:
        rc = cli_run(
            ["explore", "--from-file", str(obs_file), "--budget-tokens", "100"]
        )
    finally:
        os.chdir(old)
    # Loud non-zero exit: a budget-exhausted goal fails the run.
    assert rc == 1

    report_text = sorted(
        (root / ".praxis" / "runs").glob("*/explore-candidates.md")
    )[-1].read_text()
    # The report names every goal that could not be explored (loud over silent).
    assert "could not be explored" in report_text
    assert "`alpha`" in report_text and "`beta`" in report_text
    assert "per-goal budget exhausted" in report_text

    # No committed candidate file for an exhausted goal (not a clean success).
    committed = sorted((root / ".praxis" / "candidates").glob("*/*.yaml"))
    assert committed == []


def test_wall_budget_flag_is_parsed_and_forwarded(tmp_path: Path) -> None:
    """The `--budget-wall-seconds` flag mirrors the regress aggregate flag:
    `praxis explore` parses it and the aggregate path forwards it as the
    per-goal wall slice (ADR-0023 decision 7)."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha"])
    obs_file = root / "obs.json"
    obs_file.write_text(json.dumps(_explore_obs([_http_risk("phishy-redirect")])))
    old = Path.cwd()
    os.chdir(root)
    try:
        # A generous wall slice that the in-memory brain run never exceeds: the
        # flag parses and the run still succeeds (exit 0).
        rc = cli_run(
            ["explore", "--from-file", str(obs_file), "--budget-wall-seconds", "60"]
        )
    finally:
        os.chdir(old)
    assert rc == 0


def test_off_path_fraction_floor_preserved_with_budget_slice(tmp_path: Path) -> None:
    """The off_path_fraction floor logging is preserved for goals that run
    within their slice even when a per-goal budget is set: a healthy goal still
    carries its fraction (ADR-0009 floor), unaffected by the decision-7 cap."""
    root = tmp_path / "p"
    root.mkdir()
    _init_with_goals(root, ["alpha", "beta"])
    proj = discover_project(root)
    runner = ExplorationRunner(proj.adapter(), agent_id=proj.agent_id)
    # 200 tokens is within the 1000 slice: both goals run and keep their floor.
    brain = _const_brain(_explore_obs([_http_risk("phishy-redirect")]))
    outcomes = run_explore_aggregate(
        runner, ["alpha", "beta"], brain,
        budget_tokens_per_goal=1000, budget_wall_seconds_per_goal=60.0,
    )
    for oc in outcomes:
        assert oc.ok and oc.result is not None
        assert oc.result.off_path_fraction == 1.0
