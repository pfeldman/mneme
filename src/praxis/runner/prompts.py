"""Prompt rendering for R-mode (regression) and E-mode (exploration).

Both modes hand the agent the same kind of artifact: a structured instruction
naming the goal, the observable signals it must check (R-mode) or the risks it
must probe (E-mode), and the exact shape of the observation to emit back.

The renderers describe SHAPE, never STEPS. The agent regenerates its own steps;
this is the central Phase-0 result the project is built on (docs/01).

Free text is bounded: triggers come in as structured `HttpTrigger` /
`SequenceTrigger` and we render them deterministically. A typo in a trigger
shows up in the prompt as a literal value, not as a vague instruction.
"""
from __future__ import annotations

import json

from ..model import (
    ElementMembershipCheck,
    HttpTrigger,
    KnowledgeFile,
    ListCountDeltaCheck,
    Risk,
    SequenceTrigger,
    Signal,
    Trigger,
    Uncertainty,
)


def _format_check(sig: Signal) -> str:
    # When a signal carries a structured check (ADR-0031), tell the agent which
    # STRUCTURED FIELDS to report in its observation `observed` payload. The body
    # evaluates the check over those raw numbers (it never trusts the agent's
    # judgement that "it passed"), so a genuinely-passing run must report the
    # data, not prose. A check supersedes the value_predicate line; the two are
    # mutually exclusive in practice (a signal uses one structured path).
    check = sig.check
    if isinstance(check, ListCountDeltaCheck):
        return (
            f"\n     structured check (report counts, do NOT paraphrase): observe "
            f"the list size BEFORE the action and AFTER it, and emit "
            f'observed={{"before_count": <int>, "after_count": <int>}}. The '
            f"expected change is {check.expect_delta:+d}."
        )
    if isinstance(check, ElementMembershipCheck):
        return (
            f"\n     structured check (report membership, do NOT paraphrase): "
            f"track the concrete per-run {check.identifier_slot} and, AFTER the "
            f'action, emit observed={{"identifier": "<the concrete '
            f'{check.identifier_slot} you saw>", "present": <true|false>}}. The '
            f"expected state is {check.expect}."
        )
    return ""


def _format_signal(sig: Signal, idx: int) -> str:
    # A structured check (ADR-0031) takes precedence over a value_predicate
    # (ADR-0030); both take precedence over free prose. Surface whichever the
    # signal carries so the agent confirms IN the shape the matcher evaluates.
    if sig.check is not None:
        structured = _format_check(sig)
    elif sig.value_predicate is not None:
        # The matcher matches the observation against the predicate by
        # containment (invariant exact, {slots} filled), so the agent must report
        # the observation as the predicate with each {slot} replaced by the
        # concrete value it saw; otherwise a genuinely-passing check cannot match.
        structured = (
            f"\n     fact (confirm in THIS exact shape, fill each {{slot}} with "
            f"the concrete value you saw): {sig.value_predicate}"
        )
    else:
        structured = ""
    return (
        f"  {idx}. [{sig.type.value}] {sig.value}{structured}"
        f"  (status={sig.status.value}, confidence={sig.confidence:.2f})"
    )


def _format_trigger(t: Trigger) -> str:
    if isinstance(t, HttpTrigger):
        body = (
            f" body/params={json.dumps(t.body_or_params, sort_keys=True)}"
            if t.body_or_params else ""
        )
        return f"HTTP {t.method} {t.path}{body}  expect: {t.expect}"
    if isinstance(t, SequenceTrigger):
        return f"SEQUENCE {t.n}x {t.action}  expect: {t.expect}"
    raise TypeError(f"unknown trigger kind: {type(t)!r}")  # defensive; schema gates


def _format_risk(risk: Risk, idx: int) -> str:
    mitig = f"\n     mitigation: {risk.mitigation}" if risk.mitigation else ""
    return (
        f"  {idx}. [{risk.id}] {risk.description}\n"
        f"     trigger: {_format_trigger(risk.trigger)}"
        f"{mitig}"
        f"  (status={risk.status.value}, confidence={risk.confidence:.2f})"
    )


def _format_uncertainty(u: Uncertainty, idx: int) -> str:
    return f"  {idx}. [{u.id}] {u.question}  (raised_by={u.raised_by})"


def render_regression_prompt(kf: KnowledgeFile, *, budget_actions: int | None = None,
                              budget_tokens: int | None = None) -> str:
    """Render the R-mode prompt for one goal.

    Hands the agent: the goal, the success signals (oracle - what to check),
    the failure signals (anti-goals - if observed, a regression), the budget.
    Auditor scenarios are NOT included: that leak path was removed in
    ADR-0009 sec 6.
    """
    success = "\n".join(_format_signal(s, i + 1) for i, s in enumerate(kf.success_signals))
    failures = kf.failure_signals or []
    failure_block = (
        "\nFailure signals (if observed = regression):\n"
        + "\n".join(_format_signal(s, i + 1) for i, s in enumerate(failures))
        if failures else ""
    )
    budget_line = _format_budget(budget_actions, budget_tokens)
    # The emit contract is aligned with the matcher (ADR-0028): the agent must
    # confirm EVERY success signal listed above and emit each observation IN that
    # signal's DECLARED type (the [type] shown), because the verdict matches a
    # believed success signal only when the observation's type equals the
    # signal's type (regression._value_matches, exact-type equality). The old
    # "type the observation by what you actually checked" instruction fought that
    # rule and made a genuinely-passing goal read UNCERTAIN. The grounding
    # guardrail LEADS the completeness instruction: confirm all is never tick
    # all; an unconfirmable signal is left unconfirmed, never fabricated, because
    # a false confirmation is the worst outcome (docs/06, ADR-0005).
    return (
        f"GOAL ({kf.goal_id}): {kf.goal}\n"
        f"App: {kf.target.app}"
        f"{(' (env=' + kf.target.environment + ')') if kf.target.environment else ''}\n"
        f"\nSuccess signals (the oracle - confirm EACH one listed):\n{success}"
        f"{failure_block}\n"
        f"\nMode: REGRESSION. Regenerate your own steps to achieve the goal. Do NOT replay\n"
        f"recorded steps. Confirm every success signal listed above: emit exactly one\n"
        f"observation per signal, and emit each observation IN that signal's declared type\n"
        f"(the [type] shown on its line: behavioral / network / accessibility / text / url /\n"
        f"visual), through write_observations. Each observation must be grounded in evidence\n"
        f"you actually saw; NEVER assert a signal just to complete the list. If you cannot\n"
        f"ground a signal in its declared type, leave it unconfirmed - do NOT fabricate one,\n"
        f"because a false confirmation is the worst possible outcome. For a signal that shows a\n"
        f"`fact (confirm in THIS exact shape...)` line, your observation value MUST match that\n"
        f"template exactly, with each {{slot}} replaced by the concrete value you actually\n"
        f"observed and everything outside the slots kept verbatim. For a signal that shows a\n"
        f"`structured check (...)` line, emit the exact `observed` object it asks for (the raw\n"
        f"counts, or the concrete identifier and its membership) - report the data you saw, do\n"
        f"NOT decide yourself whether it passed; the runner evaluates the check. Match a failure\n"
        f"signal -> regression."
        f"\n{budget_line}"
    )


def render_exploration_prompt(kf: KnowledgeFile, *, budget_actions: int | None = None,
                               budget_tokens: int | None = None) -> str:
    """Render the E-mode prompt for one goal.

    Hands the agent: the goal, the believed/contested risks with structured
    triggers, the uncertainties to investigate, the failure signals to watch
    for. The agent emits candidate observations (status=contested) back through
    the store; promotion to `believed` follows the existing diversity/source-
    independence gate (ADR-0005, ADR-0008).
    """
    risks = [r for r in (kf.risks or [])
             if r.status.value in ("believed", "contested")]
    uncertainties = [u for u in (kf.uncertainties or []) if not u.resolved]
    failures = kf.failure_signals or []

    risks_block = (
        "\nRisks to probe (each carries a structured trigger - execute it):\n"
        + "\n".join(_format_risk(r, i + 1) for i, r in enumerate(risks))
        if risks else "\nRisks to probe: (none known yet - this is a discovery run)"
    )
    uncert_block = (
        "\nOpen uncertainties (look for answers):\n"
        + "\n".join(_format_uncertainty(u, i + 1) for i, u in enumerate(uncertainties))
        if uncertainties else ""
    )
    failure_block = (
        "\nWatch-list (failure signals that indicate something broke):\n"
        + "\n".join(_format_signal(s, i + 1) for i, s in enumerate(failures))
        if failures else ""
    )
    budget_line = _format_budget(budget_actions, budget_tokens)
    return (
        f"GOAL ({kf.goal_id}): {kf.goal}\n"
        f"App: {kf.target.app}"
        f"{(' (env=' + kf.target.environment + ')') if kf.target.environment else ''}"
        f"{risks_block}"
        f"{uncert_block}"
        f"{failure_block}\n"
        f"\nMode: EXPLORATION. Probe the risks' triggers. If an `expect` predicate matches,\n"
        f"emit a failure_signal observation with the literal value of the predicate. If you\n"
        f"discover a new risk, emit it with a STRUCTURED trigger (HTTP or sequence form),\n"
        f"never free text. If you cannot resolve an uncertainty, write it back to the store.\n"
        f"All new candidate knowledge enters as `contested` and needs independent\n"
        f"corroboration to be believed (ADR-0008)."
        f"\n{budget_line}"
    )


def _format_budget(actions: int | None, tokens: int | None) -> str:
    parts: list[str] = []
    if tokens is not None:
        parts.append(f"{tokens} tokens")
    if actions is not None:
        parts.append(f"{actions} actions")
    if not parts:
        return "Budget: unbounded (not recommended outside tests)."
    return f"Budget: {' / '.join(parts)} max."
