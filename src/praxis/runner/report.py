"""JUnit XML + markdown report writers for R-mode runs.

JUnit XML is the CI integration point (`praxis regress` exits non-zero on any
fail, drops `.praxis/last-run.xml` for the CI to render). Markdown report is
the human-readable companion: pass/fail per goal, matched signals, notes.

Both writers are deterministic: a clean pass produces identical output across
two runs with the same observations (helps git-friendly review).
"""
from __future__ import annotations

import html
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

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
    """The one-line routing the break-vs-drift verdict implies (ADR-0023)."""
    if verdict == AggregateVerdict.OK:
        return "no action"
    if verdict == AggregateVerdict.REGRESSED:
        return "the app broke: file a bug"
    if verdict == AggregateVerdict.STALE:
        return "the app changed on purpose: re-seed the knowledge"
    return "could not reach a verdict: investigate"


def to_aggregate_markdown(reports: list[GoalReport]) -> str:
    """Render ONE aggregate break-vs-drift report over every goal (ADR-0023).

    A non-engineer reads this without knowing the runner internals: a per-goal
    verdict line carrying its evidence, plus a single roll-up. The roll-up never
    hides a regression - it leads with the loud non-OK count and names every
    REGRESSED / ERROR goal with the signal that flipped (decision 4). STALE
    goals are listed too (they route to a re-seed), but they do not make the
    run red.
    """
    if not reports:
        return "# praxis regress (aggregate)\n\n(no goals run)\n"

    n_ok = sum(1 for r in reports if r.verdict == AggregateVerdict.OK)
    n_reg = sum(1 for r in reports if r.verdict == AggregateVerdict.REGRESSED)
    n_stale = sum(1 for r in reports if r.verdict == AggregateVerdict.STALE)
    n_err = sum(1 for r in reports if r.verdict == AggregateVerdict.ERROR)
    n_fail = sum(1 for r in reports if r.fails_run)

    lines: list[str] = ["# praxis regress (aggregate)", ""]
    if n_fail:
        # Loud, named, leads the report: a single regression cannot hide behind
        # a "mostly green" summary (ADR-0023 decision 4).
        lines.append(
            f"**RUN FAILED: {n_fail} goal(s) need action "
            f"({n_reg} REGRESSED, {n_err} ERROR).**"
        )
    else:
        lines.append("**RUN PASSED: no regressions.**")
    lines.append("")
    lines.append(
        f"{n_ok} OK / {n_reg} REGRESSED / {n_stale} STALE / {n_err} ERROR "
        f"({len(reports)} goal(s))"
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

    # Spell out every goal that fails the run so the named signal is recoverable
    # from the report itself, not only from the table cell.
    failing = [r for r in reports if r.fails_run]
    if failing:
        lines.append("")
        lines.append("## Goals that fail the run")
        for r in failing:
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
