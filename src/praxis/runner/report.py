"""JUnit XML + markdown report writers for R-mode runs.

JUnit XML is the CI integration point (`praxis regress` exits non-zero on any
fail, drops `.praxis/last-run.xml` for the CI to render). Markdown report is
the human-readable companion: pass/fail per goal, matched signals, notes.

Both writers are deterministic: a clean pass produces identical output across
two runs with the same observations (helps git-friendly review).
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from ..model import HttpTrigger, KnowledgeFile, SequenceTrigger, Status
from ..store import (
    CandidateEvent,
    CandidateRiskPayload,
    CandidateUncertaintyPayload,
)
from .regression import AggregateVerdict, GoalReport, RegressionVerdict, RunResult


def _attr(value: str) -> str:
    return xml_escape(value, {'"': "&quot;"})


def to_junit_xml(results: list[RunResult], *, suite_name: str = "praxis-regress") -> str:
    """Render a JUnit XML string. One <testcase> per goal.

    `verdict=fail` becomes a <failure>; `verdict=uncertain` becomes a <skipped>
    (CI treats both as not-green but not the same; uncertain is "couldn't
    exercise the oracle", not "regression").
    """
    n = len(results)
    fails = sum(1 for r in results if r.verdict == RegressionVerdict.FAIL)
    skips = sum(1 for r in results if r.verdict == RegressionVerdict.UNCERTAIN)
    total_wall = sum(r.wall_seconds for r in results)
    cases: list[str] = []
    for r in results:
        body: list[str] = []
        if r.verdict == RegressionVerdict.FAIL:
            evidence = "; ".join(r.matched_failure) or "(no specific failure signal recorded)"
            body.append(
                f'      <failure message="{_attr("regression detected")}">'
                f"{xml_escape(evidence)}</failure>"
            )
        elif r.verdict == RegressionVerdict.UNCERTAIN:
            body.append(
                f'      <skipped message="{_attr("oracle not fully exercised")}"/>'
            )
        if r.notes:
            body.append(
                f"      <system-out>{xml_escape(chr(10).join(r.notes))}</system-out>"
            )
        body_str = "\n".join(body)
        cases.append(
            f'    <testcase name="{_attr(r.goal_id)}" '
            f'classname="{_attr(suite_name)}" '
            f'time="{r.wall_seconds:.3f}">'
            + (("\n" + body_str + "\n    ") if body_str else "")
            + "</testcase>"
        )
    cases_str = "\n".join(cases)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="{_attr(suite_name)}" '
        f'tests="{n}" failures="{fails}" skipped="{skips}" '
        f'time="{total_wall:.3f}">\n'
        f"{cases_str}\n"
        "</testsuite>\n"
    )


def write_junit_xml(results: list[RunResult], path: str | Path, *,
                     suite_name: str = "praxis-regress") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_junit_xml(results, suite_name=suite_name), encoding="utf-8")
    return p


def to_markdown(results: list[RunResult]) -> str:
    """Render a human-readable markdown report."""
    if not results:
        return "# praxis regress\n\n(no goals run)\n"
    n_pass = sum(1 for r in results if r.verdict == RegressionVerdict.PASS)
    n_fail = sum(1 for r in results if r.verdict == RegressionVerdict.FAIL)
    n_unc = sum(1 for r in results if r.verdict == RegressionVerdict.UNCERTAIN)
    total_actions = sum(r.actions for r in results)
    total_tokens = sum((r.tokens or 0) for r in results)
    lines: list[str] = [
        "# praxis regress",
        "",
        f"**{n_pass} pass / {n_fail} fail / {n_unc} uncertain**"
        f"  ({total_actions} actions, "
        f"{total_tokens if total_tokens else 'n/a'} tokens)",
        "",
    ]
    lines.append("| goal | verdict | actions | tokens | wall (s) | matched / evidence |")
    lines.append("|------|---------|---------|--------|----------|---------------------|")
    for r in results:
        evidence: str
        if r.verdict == RegressionVerdict.FAIL:
            evidence = "regression: " + (", ".join(r.matched_failure) or "(unspecified)")
        elif r.verdict == RegressionVerdict.PASS:
            evidence = f"all {len(r.matched_success)} success signals matched"
        else:
            evidence = (
                f"{len(r.matched_success)} success signals matched (need more)"
            )
        toks = str(r.tokens) if r.tokens is not None else "-"
        lines.append(
            f"| `{r.goal_id}` | **{r.verdict.value}** | {r.actions} | {toks} | "
            f"{r.wall_seconds:.2f} | {evidence} |"
        )
    for r in results:
        if r.notes:
            lines.append("")
            lines.append(f"### Notes for `{r.goal_id}`")
            for note in r.notes:
                lines.append(f"- {html.escape(note)}")
    return "\n".join(lines) + "\n"


def write_markdown_report(results: list[RunResult], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_markdown(results), encoding="utf-8")
    return p


# --- the aggregate (default-all) break-vs-drift report (ADR-0023) -----------


def _verdict_routing(verdict: AggregateVerdict) -> str:
    """The one-line routing the break-vs-drift verdict implies (ADR-0023,
    ADR-0026 decision 5).

    AUTH-EXPIRED is a DISTINCT outcome with its own routing: it is neither
    "file a bug" (REGRESSED, the app broke) nor "re-seed the knowledge" (STALE,
    the app changed on purpose). The saved session expired, so the action is to
    re-authenticate and refresh the session (ADR-0026 decision 5). Collapsing it
    into the REGRESSED or ERROR routing would lose the named, correct action.
    """
    if verdict == AggregateVerdict.OK:
        return "no action"
    if verdict == AggregateVerdict.REGRESSED:
        return "the app broke: file a bug"
    if verdict == AggregateVerdict.STALE:
        return "the app changed on purpose: re-seed the knowledge"
    if verdict == AggregateVerdict.AUTH_EXPIRED:
        return "the saved session expired: re-authenticate / refresh the session"
    return "could not reach a verdict: investigate"


def to_aggregate_markdown(reports: list[GoalReport]) -> str:
    """Render ONE aggregate break-vs-drift report over every goal (ADR-0023).

    A non-engineer reads this without knowing the runner internals: a per-goal
    verdict line carrying its evidence, plus a single roll-up. The roll-up never
    hides a regression - it leads with the loud non-OK count and names every
    REGRESSED / AUTH-EXPIRED / ERROR goal with the signal (or expired role) that
    flipped (decision 4, ADR-0026 decision 5). STALE goals are listed too (they
    route to a re-seed), but they do not make the run red.

    AUTH-EXPIRED is counted DISTINCTLY from REGRESSED and STALE (ADR-0026
    decision 5): an expired saved session is neither a broken app nor outdated
    knowledge, so it gets its own count in the roll-up and its own line in the
    failure summary naming the expired role. A run with an AUTH-EXPIRED goal is
    never reported as green / "mostly green"; it leads with RUN FAILED.
    """
    if not reports:
        return "# praxis regress (aggregate)\n\n(no goals run)\n"

    n_ok = sum(1 for r in reports if r.verdict == AggregateVerdict.OK)
    n_reg = sum(1 for r in reports if r.verdict == AggregateVerdict.REGRESSED)
    n_stale = sum(1 for r in reports if r.verdict == AggregateVerdict.STALE)
    n_err = sum(1 for r in reports if r.verdict == AggregateVerdict.ERROR)
    n_auth = sum(1 for r in reports if r.verdict == AggregateVerdict.AUTH_EXPIRED)
    n_fail = sum(1 for r in reports if r.fails_run)

    lines: list[str] = ["# praxis regress (aggregate)", ""]
    if n_fail:
        # Loud, named, leads the report: a single regression / expired session
        # cannot hide behind a "mostly green" summary (ADR-0023 decision 4,
        # ADR-0026 decision 5). AUTH-EXPIRED is named distinctly so the action
        # (re-authenticate) is not collapsed into "file a bug".
        breakdown = f"{n_reg} REGRESSED, {n_err} ERROR"
        if n_auth:
            breakdown = f"{n_reg} REGRESSED, {n_auth} AUTH-EXPIRED, {n_err} ERROR"
        lines.append(
            f"**RUN FAILED: {n_fail} goal(s) need action ({breakdown}).**"
        )
    else:
        lines.append("**RUN PASSED: no regressions.**")
    lines.append("")
    lines.append(
        f"{n_ok} OK / {n_reg} REGRESSED / {n_stale} STALE / "
        f"{n_auth} AUTH-EXPIRED / {n_err} ERROR ({len(reports)} goal(s))"
    )
    lines.append("")

    lines.append("| goal | verdict | routing | evidence |")
    lines.append("|------|---------|---------|----------|")
    for r in reports:
        evidence = html.escape(r.evidence).replace("|", "\\|")
        lines.append(
            f"| `{r.goal_id}` | **{r.verdict.value}** | "
            f"{_verdict_routing(r.verdict)} | {evidence} |"
        )

    # Spell out every goal that fails the run so the named signal (or the
    # expired role for AUTH-EXPIRED) is recoverable from the report itself, not
    # only from the table cell.
    failing = [r for r in reports if r.fails_run]
    if failing:
        lines.append("")
        lines.append("## Goals that fail the run")
        for r in failing:
            if r.verdict == AggregateVerdict.AUTH_EXPIRED:
                # AUTH-EXPIRED carries the expired ROLE in `signals` (ADR-0026
                # decision 5), not a flipped app signal. Name it as a role so the
                # human knows exactly which session to refresh.
                role = ", ".join(r.signals) if r.signals else "(role not named)"
                lines.append(
                    f"- **{r.verdict.value}** `{r.goal_id}`: {html.escape(r.evidence)} "
                    f"[expired role: {html.escape(role)}]"
                )
            else:
                named = ", ".join(r.signals) if r.signals else "(no specific signal named)"
                lines.append(
                    f"- **{r.verdict.value}** `{r.goal_id}`: {html.escape(r.evidence)} "
                    f"[signal(s): {html.escape(named)}]"
                )

    stale = [r for r in reports if r.verdict == AggregateVerdict.STALE]
    if stale:
        lines.append("")
        lines.append("## Stale goals (knowledge outdated; propose a re-seed)")
        for r in stale:
            lines.append(f"- `{r.goal_id}`: {html.escape(r.evidence)}")

    return "\n".join(lines) + "\n"


def write_aggregate_markdown(reports: list[GoalReport], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_aggregate_markdown(reports), encoding="utf-8")
    return p


# --- the explore candidate report, grouped by trigger (ADR-0023 decision 8) -


def _trigger_key(event: CandidateEvent) -> tuple[str, ...]:
    """A stable grouping key for ONE candidate observation's structured trigger.

    Two observations of the SAME finding share one structured `trigger`
    (ADR-0021 decision 4); this key is what makes them group into a single
    report row (ADR-0023 decision 8: each finding appears ONCE). The key is
    built only from the structured trigger fields (or the uncertainty's
    question), never from the per-observation event id, so N observations of one
    finding collapse to one group.

    - candidate_risk / HttpTrigger:     ("risk", "http", METHOD, PATH, EXPECT)
    - candidate_risk / SequenceTrigger: ("risk", "sequence", N, ACTION, EXPECT)
    - candidate_uncertainty:            ("uncertainty", QUESTION)
    """
    payload = event.payload
    if isinstance(payload, CandidateRiskPayload):
        trig = payload.risk.trigger
        if isinstance(trig, HttpTrigger):
            return ("risk", "http", trig.method, trig.path, trig.expect)
        if isinstance(trig, SequenceTrigger):
            return ("risk", "sequence", str(trig.n), trig.action, trig.expect)
        # Unreachable under the discriminated union, but keep the key total.
        return ("risk", trig.kind)
    if isinstance(payload, CandidateUncertaintyPayload):
        return ("uncertainty", payload.uncertainty.question)
    return ("unknown",)


def _trigger_label(event: CandidateEvent) -> str:
    """A one-line human label for a finding's structured trigger (or question)."""
    payload = event.payload
    if isinstance(payload, CandidateRiskPayload):
        trig = payload.risk.trigger
        if isinstance(trig, HttpTrigger):
            return f"{trig.method} {trig.path} -> expect: {trig.expect}"
        if isinstance(trig, SequenceTrigger):
            return f"{trig.n}x {trig.action} -> expect: {trig.expect}"
        return trig.kind
    if isinstance(payload, CandidateUncertaintyPayload):
        return payload.uncertainty.question
    return "(unknown)"


@dataclass
class CandidateGroup:
    """One finding in the explore candidate report (ADR-0023 decision 8).

    A finding is the set of observations that share one structured trigger (for
    risks) or one question (for uncertainties). It appears ONCE in the report,
    annotated with:

    - `observation_count`: how many times it was observed (one per
      CandidateEvent in the group).
    - `distinct_source_ids`: the DISTINCT `agent_identity` values that attest to
      it. N observations from the same `agent_identity` count as ONE source
      (ADR-0008), never as N duplicate entries; `source_count` is therefore
      `len(distinct_source_ids)`, not `observation_count`.
    - `believed`: True only when the finding earned `believed` by
      diversity-or-seed (ADR-0005, ADR-0008, ADR-0014) at the projection. A
      finding is never believed by observation count alone.

    `kind` is "risk" or "uncertainty"; `description` is the underlying finding
    text; `trigger_label` is the structured trigger (or the question) rendered
    for a human.
    """

    kind: str
    description: str
    trigger_label: str
    observation_count: int
    distinct_source_ids: set[str]
    believed: bool
    goal_id: str
    notes: list[str] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len(self.distinct_source_ids)


def group_candidates_by_trigger(
    events: list[CandidateEvent],
    *,
    seed: KnowledgeFile | None = None,
) -> list[CandidateGroup]:
    """Group candidate observations by their structured trigger (decision 8).

    Each finding appears ONCE. The observation count is the number of
    CandidateEvents sharing the trigger; the distinct-source count is the number
    of distinct `agent_identity` values across those events (ADR-0008: N same-
    agent observations are ONE source). The `believed` flag reuses the SAME
    diversity-or-seed promotion gate `merge.candidates.project_candidates`
    enforces, so the report never invents a relaxed, count-based promotion: a
    finding is believed only when at least two distinct sources AND at least two
    distinct evidence types agree, or a matching seed supplies the second
    independent source.

    `seed` is the goal's believed KnowledgeFile (if any); a matching seed risk /
    uncertainty is one independent source for the promotion check, exactly as in
    the projection. Stable order: groups sort by (kind, trigger_label) so two
    renders of the same committed tree are byte-identical (git-friendly review).
    """
    # Import here to avoid a runner -> merge import cycle at module load; merge
    # imports model + store, the runner imports merge only at use sites.
    from ..merge.candidates import project_candidates

    # Project once over all events (with the seed) to get the diversity-or-seed
    # status per candidate id. The projection is the single source of the
    # promotion rule; the report only annotates with counts, it does not re-judge
    # promotion.
    projected = project_candidates(events, seed=seed)
    believed_ids = {
        pc.candidate_id for pc in projected if pc.status == Status.BELIEVED
    }
    # The FULL distinct-source set the projection used to promote each believed
    # candidate id. The projection groups by candidate id and the promotion gate
    # spans every source attesting that id (and any matching seed); the report
    # groups by structured trigger, a finer unit. When one promoted candidate id
    # carries two different trigger kinds from two sources, the trigger grouping
    # would otherwise show each row with only the sources present in THAT trigger
    # group (often one), contradicting a `believed` verdict that decision 8 says
    # requires >=2 distinct attesting sources. We carry the projection's full
    # source set onto a believed row so the displayed source count and the
    # believed determination use the SAME grouping unit and the report never
    # shows believed with fewer than two attesting sources (ADR-0023 decision 8).
    projection_sources_by_id = {
        pc.candidate_id: pc.distinct_source_ids for pc in projected
    }

    groups: dict[tuple[str, ...], list[CandidateEvent]] = {}
    for ev in events:
        groups.setdefault(_trigger_key(ev), []).append(ev)

    out: list[CandidateGroup] = []
    for key, evs in groups.items():
        first = evs[0]
        payload = first.payload
        if isinstance(payload, CandidateRiskPayload):
            kind = "risk"
            description = payload.risk.description
        elif isinstance(payload, CandidateUncertaintyPayload):
            kind = "uncertainty"
            description = payload.uncertainty.question
        else:  # pragma: no cover - total over the discriminated union
            kind = "unknown"
            description = ""
        sources = {ev.agent_identity for ev in evs}
        # A group is believed when ANY candidate id within it earned believed at
        # the projection (one trigger maps to one candidate id in practice; the
        # any() keeps it correct if two ids ever share a trigger).
        believed = any(ev.candidate_id in believed_ids for ev in evs)
        if believed:
            # Show the SAME source set the promotion gate used, not just the
            # sources in this one trigger group. This keeps the believed verdict
            # and the displayed `distinct sources` count from contradicting each
            # other at the same-id / multiple-trigger-kind edge (decision 8).
            for ev in evs:
                if ev.candidate_id in believed_ids:
                    sources = sources | projection_sources_by_id.get(
                        ev.candidate_id, set()
                    )
        out.append(CandidateGroup(
            kind=kind,
            description=description,
            trigger_label=_trigger_label(first),
            observation_count=len(evs),
            distinct_source_ids=sources,
            believed=believed,
            goal_id=first.goal_id,
        ))
    out.sort(key=lambda g: (g.kind, g.trigger_label, g.goal_id))
    return out


def to_candidate_markdown(
    groups: list[CandidateGroup],
    *,
    off_path_fractions: dict[str, float] | None = None,
    errors: dict[str, str] | None = None,
) -> str:
    """Render the explore candidate report (ADR-0023 decision 8).

    Observations are GROUPED by their structured trigger: each finding appears
    ONCE, annotated with the observation count and the DISTINCT source count
    (N same-`agent_identity` observations are ONE source, never N rows). The
    `off_path_fractions` map carries the ADR-0009 E-mode kill-criterion floor
    per goal so the operator can see whether E-mode stayed off the happy path;
    `errors` names any goal that could not be explored (loud over silent).
    """
    lines: list[str] = ["# praxis explore (candidates)", ""]

    n_findings = len(groups)
    n_believed = sum(1 for g in groups if g.believed)
    n_contested = n_findings - n_believed
    lines.append(
        f"**{n_findings} finding(s): {n_believed} believed / "
        f"{n_contested} contested**"
    )
    lines.append("")

    if off_path_fractions:
        # ADR-0009 floor logging: surface off_path_fraction per goal so a run
        # that collapsed into R-mode (fraction near 0) is visible, not hidden.
        lines.append("## off_path_fraction (ADR-0009 floor)")
        for gid in sorted(off_path_fractions):
            lines.append(f"- `{gid}`: {off_path_fractions[gid]:.2f}")
        lines.append("")

    if errors:
        lines.append("## Goals that could not be explored")
        for gid in sorted(errors):
            lines.append(f"- `{gid}`: {html.escape(errors[gid])}")
        lines.append("")

    if not groups:
        lines.append("(no candidate findings)")
        return "\n".join(lines) + "\n"

    lines.append(
        "| finding | kind | trigger | observations | distinct sources | status |"
    )
    lines.append(
        "|---------|------|---------|--------------|------------------|--------|"
    )
    for g in groups:
        status = "believed" if g.believed else "contested"
        desc = html.escape(g.description).replace("|", "\\|")
        trig = html.escape(g.trigger_label).replace("|", "\\|")
        lines.append(
            f"| {desc} | {g.kind} | {trig} | {g.observation_count} | "
            f"{g.source_count} | **{status}** |"
        )
    return "\n".join(lines) + "\n"


def write_candidate_markdown(
    groups: list[CandidateGroup],
    path: str | Path,
    *,
    off_path_fractions: dict[str, float] | None = None,
    errors: dict[str, str] | None = None,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        to_candidate_markdown(
            groups, off_path_fractions=off_path_fractions, errors=errors,
        ),
        encoding="utf-8",
    )
    return p
