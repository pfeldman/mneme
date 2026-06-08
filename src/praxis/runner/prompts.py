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
    HttpTrigger,
    KnowledgeFile,
    Risk,
    SequenceTrigger,
    Signal,
    Trigger,
    Uncertainty,
)


def _format_signal(sig: Signal, idx: int) -> str:
    return (
        f"  {idx}. [{sig.type.value}] {sig.value}"
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
        f"because a false confirmation is the worst possible outcome. Match a failure signal\n"
        f"-> regression."
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
