"""R-mode (regression) runner.

Contract:
  inputs  - believed success_signals + failure_signals for one or more goals.
  outputs - per goal: pass / fail / uncertain verdict + observations + a run
            record. The runner does NOT drive a browser; it renders prompts
            and reads the store. The executor (subscription Claude Code path
            or API-key path) feeds observations into the store via
            `adapter.write_observations(...)`; the runner then computes the
            verdict deterministically from those observations.

The verdict logic is deliberately small (ADR-0009 sec 3 + AGENTS.md
non-negotiable 5): a failure signal observed = fail; all success signals
observed = pass; otherwise = uncertain. We do NOT call the oracle inside the
runner: the oracle gates BELIEF (which signals become trusted across runs),
not the per-run verdict. Mixing them silently is the wrong-oracle vector
docs/06 warns about.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable

from ..adapters.spi import KnowledgeAdapter
from ..merge.decay import DecayConfig, _parse_semver, is_observation_staled
from ..model import KnowledgeFile, Signal, SignalType, Status
from ..model.predicate import _STOPWORDS
from ..store import ObservedSignal
from ._parallel import run_partitioned
from .prompts import render_regression_prompt


class RegressionVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"
    # The run could not authenticate: the goal expected an authenticated scope
    # (ADR-0017 auth_state) but the run observed a login wall / logged-out
    # browser (the saved session expired; ADR-0026 decision 5). This is NOT a
    # failure of the app (FAIL/REGRESSED) and NOT thin oracle evidence
    # (UNCERTAIN); it is a distinct condition routed ahead of all three.
    AUTH_EXPIRED = "auth_expired"


class AggregateVerdict(str, Enum):
    """The break-vs-drift verdict an aggregate (or single-goal) regress run
    ships PER GOAL (ADR-0023 decision 3).

    - OK:        believed success signals observed, no failure signal fired.
    - REGRESSED: a believed success signal is now absent OR a failure signal
                 fired. The APP broke; a real bug. File it against the app.
    - STALE:     live behavior diverges in a way consistent with an INTENTIONAL
                 app change (a healthy equivalent observed, or the goal's
                 anchored observed_app_version is behind the live app per the
                 ADR-0013 decay anchor). The KNOWLEDGE is outdated, not the app.
    - ERROR:     the run could not reach a verdict for the goal (the adapter
                 threw, the goal exhausted its per-goal budget slice). NOT
                 silently skipped and NOT counted OK; it fails the run loudly
                 (ADR-0023 decision 4 + decision 7).
    - AUTH_EXPIRED: the goal expected an authenticated scope (ADR-0017
                 auth_state) but the run hit a login wall / logged-out browser
                 (the saved session expired; ADR-0026 decision 5). It is NOT a
                 regression (the app did not break) and NOT stale knowledge; it
                 is "the run could not authenticate". A distinct non-OK outcome
                 that fails the run loudly, naming the goal and the expired
                 role, never collapsed into a green OK and never a false
                 REGRESSED.

    REGRESSED, ERROR, and AUTH_EXPIRED are the non-OK verdicts that fail the
    whole run. STALE is non-OK in the routing sense (it needs a human re-seed)
    but it is NOT a regression: the app did not break, so STALE alone does not
    fail the run. OK is the only verdict that needs no follow-up.
    """

    OK = "OK"
    REGRESSED = "REGRESSED"
    STALE = "STALE"
    ERROR = "ERROR"
    AUTH_EXPIRED = "AUTH-EXPIRED"

    @property
    def is_ok(self) -> bool:
        return self is AggregateVerdict.OK

    @property
    def fails_run(self) -> bool:
        """A REGRESSED, ERROR, or AUTH_EXPIRED goal fails the run loudly
        (ADR-0023 decision 4, ADR-0026 decision 5).

        STALE does not fail the run: the app changed on purpose, the knowledge
        is outdated, and the fix is a human re-seed, never a red CI gate.
        AUTH_EXPIRED DOES fail the run: a run that could not authenticate is a
        loud non-OK outcome (the saved session must be refreshed), never a
        silent green and never mislabeled REGRESSED.
        """
        return self in (
            AggregateVerdict.REGRESSED,
            AggregateVerdict.ERROR,
            AggregateVerdict.AUTH_EXPIRED,
        )


@dataclass
class RunResult:
    """The per-goal outcome of an R-mode run."""

    goal_id: str
    verdict: RegressionVerdict
    actions: int
    tokens: int | None
    wall_seconds: float
    observed_signals: list[ObservedSignal] = field(default_factory=list)
    matched_success: list[str] = field(default_factory=list)
    matched_failure: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # The brain may report that, although the literal believed success signals
    # were not all matched, it observed a HEALTHY EQUIVALENT of the success path
    # (the app changed on purpose; ADR-0023 decision 3, the STALE drift case).
    # This is execution provenance the brain emits, never a stored field; the
    # aggregate classifier reads it to route a non-PASS run to STALE vs
    # REGRESSED. Defaults False so an absent flag is treated as "no equivalent".
    healthy_equivalent_observed: bool = False
    # Whether the run drove the browser as an authenticated session (ADR-0026
    # decision 5, Open decision 2 resolved): the brain reports `authenticated`
    # in its observation payload (True = ran authenticated, False = hit a login
    # wall / logged out). The aggregate classifier reads it to route a goal that
    # expected an authenticated scope but observed authenticated=False to
    # AUTH-EXPIRED, ahead of FAIL/REGRESSED/PASS/UNCERTAIN. Defaults True so an
    # absent flag (and the anonymous-scope path) is treated as "no auth wall",
    # leaving existing runs and tests unaffected.
    authenticated: bool = True
    # ADR-0033 decision 4: the run's VOID confirmations, named with reasons
    # (unknown ref, empty evidence, conflicting duplicate, failed tier gate).
    # `classify_goal` appends them to the GoalReport evidence so a REGRESSED
    # produced by a sloppy envelope is distinguishable from one produced by
    # the app, from the run output alone. Defaults empty: absent on every
    # pre-ADR-0033 path.
    void_confirmations: list[str] = field(default_factory=list)
    # ADR-0033 decision 5: the run's ADVISORY tripwire flag messages (off-topic
    # containment, parrot evidence, type-vocabulary). Recorded, never gating.
    confirmation_flags: list[str] = field(default_factory=list)
    # ADR-0033 decision 7: True when a believed FREE-TEXT success signal was
    # matched through the legacy Jaccard paraphrase path (an unsolicited or
    # legacy-envelope observation, not a ref-bound confirmation). The report
    # flags it so the remaining roulette is visible; the fallback is removed
    # after one transition release.
    paraphrase_matched: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# An executor receives the rendered prompt and returns what the agent observed.
# Tests pass a fake executor; the LOCAL_RUN protocol wires a real one through
# Claude Code + Playwright MCP; an API-key path can wire an LLM-driven loop.
ExecutorResult = dict
Executor = Callable[[str], ExecutorResult]


# Floor for word-overlap between a paraphrased agent observation and a
# SEED signal value (success_signals / failure_signals from the
# knowledge file). Phase-1 product surface: this decides whether
# `praxis regress` counts a real-world observation as matching the
# documented oracle. Tuned so "sign-out becomes available" matches
# "a sign-out action becomes available" (most content words in common).
#
# DELIBERATELY DIFFERENT from `experiments.regression_recall.metrics.
# PARAPHRASE_FLOOR` (0.6), which adjudicates observations against a
# pre-registered manifest in the experiment harness. The runner is
# lenient (real agent paraphrase varies a lot); the experiment matcher
# is strict (the manifest pins canonical phrasing to keep the falsifier
# rigorous). Both fall back to the LLM-judge for ambiguous cases.
_PARAPHRASE_THRESHOLD = 0.5

# `_STOPWORDS` is the shared tokenizer floor, defined canonically in
# `model.predicate` (imported above) so the free-text Jaccard path here and the
# structured predicate's invariant check use one set (ADR-0030 decision 6).


def _tokens(s: str) -> set[str]:
    out: set[str] = set()
    cur: list[str] = []
    for ch in s.lower():
        if ch.isalnum() or ch == "/":
            cur.append(ch)
        else:
            if cur:
                out.add("".join(cur))
                cur.clear()
    if cur:
        out.add("".join(cur))
    return {t for t in out if t and t not in _STOPWORDS}


def _value_matches(observed: ObservedSignal, target: Signal) -> bool:
    """Does the observation match the target signal?

    Exact-type equality gates first, unchanged and never relaxed (ADR-0028): a
    structured predicate or check NEVER loosens the type guard. Then the matcher
    dispatches on the TARGET, in order check -> value_predicate -> Jaccard
    (ADR-0031 decision 4, ADR-0030 decision 4):

      - structured CHECK target -> evaluate the typed check against the OBSERVED
        structured payload (ADR-0031 decision 5). `evaluate_check` FAILS CLOSED
        on a missing or malformed observation and is STRICTER than every string
        path: a no-op delta or a still-present element is a hard non-match, no
        false PASS. An unknown check kind cannot reach here (rejected at the
        write boundary, decision 6).

      - structured PREDICATE target -> evaluate the predicate against the
        OBSERVED value (ADR-0030 decision 2). The invariant text matches EXACTLY
        (case-folded + whitespace-normalized) and each declared slot must be
        FILLED (and, with a declared shape, shaped); Jaccard is NOT computed.
        This is STRICTER than Jaccard everywhere except the one declared
        instance-token axis where Jaccard produced a false negative (decision
        3). A malformed predicate cannot reach here: it is rejected at the write
        boundary (decision 6), so a parse failure is a hard non-match, never a
        silent fall-through to the looser free-text path.

      - free-text target -> the legacy Jaccard path, unchanged (ADR-0028). Value
        strings are short semantic phrases; exact equality is too strict (agents
        paraphrase) and substring containment misses common cases, so Jaccard on
        content tokens at `_PARAPHRASE_THRESHOLD` decides.
    """
    if observed.type != target.type:
        return False

    if target.check is not None:
        # Structured check: evaluate the typed assertion over the OBSERVED
        # structured payload, no predicate and no Jaccard (ADR-0031 decision 4).
        # `evaluate_check` fails closed on a None / malformed payload, so an
        # un-reportable check is a hard non-match, never a looser fall-through.
        from ..model.check import evaluate_check

        return evaluate_check(target.check, observed.observed)

    if target.value_predicate is not None:
        # Structured path: evaluate the predicate, no Jaccard (decision 2).
        from ..model.predicate import PredicateError, parse

        try:
            predicate = parse(target.value_predicate)
        except PredicateError:
            # The write boundary rejects a malformed predicate (decision 6), so
            # this is unreachable for stored knowledge. Treat any parse failure
            # as a hard NON-match anyway: a predicate that cannot be evaluated
            # must never silently fall through to the looser Jaccard path.
            return False
        return predicate.evaluate(observed.value)

    a = _tokens(observed.value)
    b = _tokens(target.value)
    if not a or not b:
        return False
    inter = len(a & b)
    union = len(a | b)
    return (inter / union) >= _PARAPHRASE_THRESHOLD


# --- ADR-0033: confirmation by identity with mandatory evidence -------------
#
# An ENUMERATED seed signal (the prompt lists it with a stable ref: S1..Sn for
# success, F1..Fm for failure, positional in the run's KnowledgeFile snapshot)
# is confirmed BY REF, never by re-matching text. The runner binds the agent's
# `{ref, present, evidence}` answer to the seed deterministically and stamps
# the seed's declared type and value onto the bound observation (decision 2,
# the ADR-0008 provenance-stamping posture): the agent never restates seed
# text, so neither the paraphrase roulette nor the parrot channel exists for
# an enumerated seed. Identity replaces only the BINDING; every grounding
# evaluation still gates after it, fail-closed (decision 3): a `check` target
# still evaluates over the structured `observed` payload (ADR-0031), a
# `value_predicate` target evaluates over the EVIDENCE string (ADR-0030
# semantics), and a free-text target requires non-empty evidence. A malformed
# confirmation (empty evidence on present:true, unknown ref, conflicting
# duplicate) is VOID: loud, named in the run output and the persisted record,
# never a match (decision 4). Jaccard survives ONLY for unsolicited
# observations (decision 7).

# Flags are body-computed audit markers on a bound observation. A flag starting
# with `void:` excludes the observation from verdict matching (fail closed);
# every other flag is the ADR-0033 decision 5 ADVISORY tripwire surface: it is
# recorded in the non-promotable regress record and the report and NEVER
# changes a verdict. Promoting a tripwire to a gate requires a future ADR with
# live flag data (the measured terse-honest-evidence case, containment 0.235,
# proves a day-one gate misfires).
_VOID_FLAG_PREFIX = "void:"

# The off-topic tripwire floor: evidence-vs-seed token containment
# (|evidence & seed| / |seed|, over the runner's own `_tokens`). Calibration
# measured live (tasks/signal-matching-redesign/analysis.md): both real
# evidence strings score 0.56+, a deliberately off-topic string scores 0.0,
# and a TERSE honest evidence string scores 0.235 - which is exactly why the
# floor sits at 0.15 and why this flags instead of gates.
_OFFTOPIC_CONTAINMENT_FLOOR = 0.15

_OFFTOPIC_FLAG = "off-topic-evidence"
_PARROT_FLAG = "parrot-evidence"
_TYPE_VOCAB_FLAG = "type-vocabulary"

# Type-vocabulary tripwire shapes (advisory, deliberately coarse: a false void
# is a false REGRESSED, so these only ever FLAG): a `network` confirmation
# whose evidence names neither a status-shaped token (200, 404, 2xx) nor a
# URL-shaped token; a `url` confirmation naming no path-like token.
_STATUS_TOKEN_RE = re.compile(r"\b[1-5](?:[0-9]{2}|xx)\b", re.IGNORECASE)
_URLISH_RE = re.compile(
    r"https?://|www\.|[a-z0-9-]+(?:\.[a-z0-9-]+){2,}|/[a-z0-9_.~-]+",
    re.IGNORECASE,
)


def _is_void(o: ObservedSignal) -> bool:
    return any(f.startswith(_VOID_FLAG_PREFIX) for f in (o.flags or []))


def _ref_table(kf: KnowledgeFile) -> dict[str, tuple[str, Signal]]:
    """The run's ref -> (kind, seed) table, positional over the SAME snapshot
    `render_regression_prompt` enumerated, so prompt and binding cannot skew
    within a run (ADR-0033 decision 1)."""
    table: dict[str, tuple[str, Signal]] = {}
    for i, s in enumerate(kf.success_signals):
        table[f"S{i + 1}"] = ("success", s)
    for i, s in enumerate(kf.failure_signals or []):
        table[f"F{i + 1}"] = ("failure", s)
    return table


def _tripwire_flags(seed: Signal, evidence: str) -> list[str]:
    """The ADVISORY tripwire flags for one FREE-TEXT confirmation (ADR-0033
    decision 5). Flags never change a verdict; they make a suspicious green
    LOUD AND TRACEABLE in the audit record (docs/06)."""
    flags: list[str] = []
    ev = _tokens(evidence)
    sd = _tokens(seed.value)
    if sd and (len(ev & sd) / len(sd)) < _OFFTOPIC_CONTAINMENT_FLOOR:
        flags.append(_OFFTOPIC_FLAG)
    if not (ev - sd):
        # Zero novel content tokens: real evidence names per-run concrete
        # detail; a copy of the seed names none.
        flags.append(_PARROT_FLAG)
    if seed.type == SignalType.NETWORK:
        if not (_STATUS_TOKEN_RE.search(evidence) or _URLISH_RE.search(evidence)):
            flags.append(_TYPE_VOCAB_FLAG)
    elif seed.type == SignalType.URL:
        if "/" not in evidence:
            flags.append(_TYPE_VOCAB_FLAG)
    return flags


def bind_confirmations(
    kf: KnowledgeFile,
    raw_confirmations: list[Any],
    *,
    agent_id: str = "praxis-agent",
) -> tuple[list[ObservedSignal], list[str]]:
    """Bind the envelope's ref-tagged confirmations to their seeds by IDENTITY
    (ADR-0033 decisions 1-5). Returns `(bound_observations, void_messages)`.

    Every bindable answer becomes an `ObservedSignal` whose `type` and `value`
    are SYSTEM-STAMPED from the seed (decision 2); a void answer that bound to
    a seed rides along flagged `void:*` so the persisted record keeps it, but
    the verdict NEVER counts it (fail closed). An unbindable answer (unknown
    ref, malformed entry) produces only a void message: there is no seed to
    stamp. NO Jaccard runs anywhere on this path.
    """
    table = _ref_table(kf)
    voids: list[str] = []
    out: list[ObservedSignal] = []

    # Duplicate scan (decision 4): a ref answered more than once with
    # CONFLICTING `present` values voids every answer for that ref; an
    # agreeing duplicate keeps the first answer and voids the redundant
    # copies (they carry no new claim, and a void is never a green).
    presents_by_ref: dict[str, set[bool]] = {}
    for entry in raw_confirmations:
        if isinstance(entry, dict) and isinstance(entry.get("ref"), str):
            presents_by_ref.setdefault(entry["ref"], set()).add(
                bool(entry.get("present", False))
            )
    conflicting = {r for r, p in presents_by_ref.items() if len(p) > 1}
    seen_refs: set[str] = set()

    for entry in raw_confirmations:
        if not isinstance(entry, dict):
            voids.append(f"malformed confirmation entry (not an object): {entry!r}")
            continue
        ref = entry.get("ref")
        if not isinstance(ref, str) or ref not in table:
            voids.append(f"unknown ref {ref!r}: no enumerated signal has it")
            continue
        kind, seed = table[ref]
        claimed_kind = entry.get("kind")
        present = bool(entry.get("present", False))
        evidence_raw = entry.get("evidence")
        evidence = evidence_raw.strip() if isinstance(evidence_raw, str) else ""

        flags: list[str] = []
        if ref in conflicting:
            msg = (f"{ref}: duplicate answers with conflicting present values; "
                   f"all answers for {ref} are void")
            voids.append(msg)
            flags.append(f"{_VOID_FLAG_PREFIX}conflicting-duplicate")
        elif ref in seen_refs:
            msg = f"{ref}: redundant duplicate answer (first answer kept)"
            voids.append(msg)
            flags.append(f"{_VOID_FLAG_PREFIX}redundant-duplicate")
        elif isinstance(claimed_kind, str) and claimed_kind != kind:
            # A ref into the failure list claimed as success (or vice versa).
            voids.append(
                f"{ref}: claimed kind {claimed_kind!r} contradicts the "
                f"enumerated {kind} list"
            )
            flags.append(f"{_VOID_FLAG_PREFIX}kind-mismatch")
        elif present and not evidence:
            # Evidence is MANDATORY for a present:true confirmation
            # (decision 4): an empty tick is void, loud, unconfirmed.
            voids.append(f"{ref}: present:true with empty/missing evidence")
            flags.append(f"{_VOID_FLAG_PREFIX}empty-evidence")
        elif present and seed.check is not None:
            # Tier gate (decision 3 / ADR-0031): the structured check still
            # evaluates over the structured payload, fail-closed. The ref only
            # binds; it never substitutes for the check.
            from ..model.check import evaluate_check

            observed_payload = entry.get("observed")
            payload = observed_payload if isinstance(observed_payload, dict) else None
            if not evaluate_check(seed.check, payload):
                voids.append(
                    f"{ref}: structured check did not hold over the reported "
                    f"`observed` payload (missing, malformed, or failing)"
                )
                flags.append(f"{_VOID_FLAG_PREFIX}check-failed")
        elif present and seed.value_predicate is not None:
            # Tier gate (decision 3 / ADR-0030): the predicate evaluates over
            # the EVIDENCE string, fail-closed. The ref never substitutes.
            from ..model.predicate import PredicateError, parse

            try:
                holds = parse(seed.value_predicate).evaluate(evidence)
            except PredicateError:
                holds = False
            if not holds:
                voids.append(
                    f"{ref}: evidence does not satisfy the signal's "
                    f"value_predicate (invariant missing or slot unfilled)"
                )
                flags.append(f"{_VOID_FLAG_PREFIX}predicate-failed")
        elif present and seed.check is None and seed.value_predicate is None:
            # Free-text tier: confirmed (ref + non-empty evidence). Record the
            # advisory tripwires (decision 5), never gating.
            flags.extend(_tripwire_flags(seed, evidence))

        seen_refs.add(ref)
        observed_payload = entry.get("observed")
        out.append(ObservedSignal(
            kind=kind,  # type: ignore[arg-type]
            type=seed.type,
            value=seed.value,  # SYSTEM-STAMPED from the seed (decision 2)
            present=present,
            source_type="agent",  # type: ignore[arg-type]
            source_id=agent_id,
            observed=observed_payload if isinstance(observed_payload, dict) else None,
            ref=ref,
            evidence=evidence or None,
            flags=flags or None,
        ))
    return out, voids


def verdict_from_observations(
    kf: KnowledgeFile,
    observations: Iterable[ObservedSignal],
) -> tuple[RegressionVerdict, list[str], list[str]]:
    """Compute the verdict for one goal from its observations.

    Returns (verdict, matched_success_values, matched_failure_values).

    Rule (ADR-0009):
      - any failure signal observed as `present=True` -> FAIL
      - all believed success signals observed as `present=True` -> PASS
      - otherwise -> UNCERTAIN (oracle could not be exercised; not a regression
        but also not a clean pass)

    Matching is two-channel (ADR-0033 decision 7):
      - a REF-BOUND observation (`ref` set; produced by `bind_confirmations`,
        which already applied the per-tier grounding gates fail-closed and
        system-stamped the seed's type/value) matches its target by IDENTITY:
        the ref IS the binding, no `_value_matches`, no Jaccard. A `void:*`
        flagged answer NEVER matches (decision 4: a void is never a green).
      - an UNSOLICITED observation (no ref: extra failure evidence, an old
        envelope) matches through the legacy `_value_matches` path, unchanged.
    """
    all_obs = list(observations)
    # First answer wins per ref: a redundant duplicate was void-flagged at bind
    # time and must not shadow the first valid answer (conflicting duplicates
    # are ALL void, so order is irrelevant for them).
    bound: dict[str, ObservedSignal] = {}
    for o in all_obs:
        if o.ref is not None:
            bound.setdefault(o.ref, o)
    obs = [o for o in all_obs if o.present and o.ref is None]

    def _ref_confirmed(ref: str, kind: str) -> bool:
        o = bound.get(ref)
        return (o is not None and o.present and o.kind == kind
                and not _is_void(o))

    matched_success: list[str] = []
    matched_failure: list[str] = []

    failure_seeds = list(kf.failure_signals or [])
    for i, ft in enumerate(failure_seeds):
        if ft.status not in (Status.BELIEVED, Status.CONTESTED):
            continue
        if _ref_confirmed(f"F{i + 1}", "failure") or any(
            _value_matches(o, ft) and o.kind == "failure" for o in obs
        ):
            matched_failure.append(ft.value)
    if matched_failure:
        return RegressionVerdict.FAIL, matched_success, matched_failure

    n_believed_success = 0
    for i, st in enumerate(kf.success_signals):
        if st.status != Status.BELIEVED:
            continue
        n_believed_success += 1
        if _ref_confirmed(f"S{i + 1}", "success") or any(
            _value_matches(o, st) and o.kind == "success" for o in obs
        ):
            matched_success.append(st.value)

    if n_believed_success and len(matched_success) == n_believed_success:
        return RegressionVerdict.PASS, matched_success, matched_failure
    return RegressionVerdict.UNCERTAIN, matched_success, matched_failure


@dataclass
class _RunContext:
    """What the executor returned, as a typed wrapper around the dict."""

    observations: list[ObservedSignal]
    actions: int
    tokens: int | None
    notes: list[str]
    healthy_equivalent_observed: bool
    authenticated: bool
    # ADR-0033: the raw ref-tagged `confirmations` array (each entry
    # `{ref, present, evidence, observed?}`), bound to seeds by `run_one` via
    # `bind_confirmations` (binding needs the KnowledgeFile snapshot, which
    # this parse does not have). `has_confirmations` distinguishes an empty
    # array (a new brain that confirmed nothing) from a LEGACY envelope with
    # no `confirmations` key at all (decision 7: the legacy path is flagged).
    raw_confirmations: list[Any] = field(default_factory=list)
    has_confirmations: bool = False


def _parse_executor_result(
    raw: ExecutorResult, *, agent_id: str = "praxis-agent",
) -> _RunContext:
    obs_raw = raw.get("observations", [])
    obs: list[ObservedSignal] = []
    for o in obs_raw:
        if isinstance(o, ObservedSignal):
            obs.append(o)
        else:
            # Provenance is stamped by the SYSTEM, not supplied by the agent: a
            # brain (claude -p, the skill, the API-key agent) emits what it saw
            # (kind / type / value / present), and the runner attributes it to
            # the run's agent identity. source_id = agent_identity is the ADR-0008
            # rule, and AGENTS.md forbids the agent inventing a generated id. So
            # default source_type=agent and source_id=agent_id when the
            # observation omits them; an explicit value (the --from-file fixtures,
            # a seeded human observation) still wins.
            o = dict(o)
            o.setdefault("source_type", "agent")
            o.setdefault("source_id", agent_id)
            obs.append(ObservedSignal.model_validate(o))
    confirmations = raw.get("confirmations")
    return _RunContext(
        observations=obs,
        actions=int(raw.get("actions", 0)),
        tokens=raw.get("tokens"),
        notes=list(raw.get("notes", [])),
        healthy_equivalent_observed=bool(raw.get("healthy_equivalent_observed", False)),
        # Minimal, additive: default True so an absent flag is "ran
        # authenticated / unknown", leaving the anonymous-scope path and every
        # existing payload unaffected (ADR-0026 Open decision 2).
        authenticated=bool(raw.get("authenticated", True)),
        # ADR-0033: additive. An envelope with no `confirmations` key is the
        # legacy shape and routes through the unchanged Jaccard path, flagged
        # (decision 7).
        raw_confirmations=list(confirmations) if isinstance(confirmations, list) else [],
        has_confirmations=isinstance(confirmations, list),
    )


class RegressionRunner:
    """Runs R-mode across one or more goals using an adapter + executor.

    The runner is a coordinator: it asks the adapter for believed knowledge,
    renders the prompt, calls the executor (which is where the agent actually
    runs), computes the verdict, and emits a RunResult. It does NOT persist agent
    observations as promotable evidence by default (ADR-0029 defect A): regress
    reads the believed oracle and writes a verdict, never grows the believed set.

    The executor protocol is deliberately small (one function, one dict in,
    one dict out) so the LOCAL_RUN.md subscription path and an API-key path
    can satisfy it without growing the runner.
    """

    def __init__(self, adapter: KnowledgeAdapter, *, agent_id: str = "praxis-regress",
                 observed_app_version: str | None = None,
                 base_url: str | None = None) -> None:
        self.adapter = adapter
        self.agent_id = agent_id
        self.observed_app_version = observed_app_version
        # The run's deployment URL (ADR-0035 decision 3), a plain optional
        # string the prompt renderer turns into the "App under test:" line.
        # The runner never interprets it and never writes it to the store;
        # an empty string counts as unset (the ADR-0034 posture).
        self.base_url = base_url if base_url else None

    def run_one(self, goal_id: str, executor: Executor, *,
                budget_actions: int | None = None,
                budget_tokens: int | None = None,
                persist_observations: bool = False) -> RunResult:
        kf = self.adapter.read_knowledge(goal_id)
        if kf is None:
            raise ValueError(
                f"no believed knowledge for goal {goal_id!r}; seed it with `praxis learn`"
            )

        prompt = render_regression_prompt(
            kf, budget_actions=budget_actions, budget_tokens=budget_tokens,
            base_url=self.base_url,
        )

        t0 = time.monotonic()
        started_at = datetime.now(timezone.utc)
        raw = executor(prompt)
        wall = time.monotonic() - t0
        ended_at = datetime.now(timezone.utc)

        ctx = _parse_executor_result(raw, agent_id=self.agent_id)

        # ADR-0033: bind the ref-tagged confirmations to their seeds by
        # IDENTITY (type/value system-stamped from the seed, per-tier grounding
        # gates applied fail-closed, voids flagged loud). The bound observations
        # join the envelope so the verdict, the RunResult, and the persisted
        # regress record all carry them; the unsolicited observations keep the
        # unchanged legacy matching path (decision 7).
        bound, voids = bind_confirmations(
            kf, ctx.raw_confirmations, agent_id=self.agent_id,
        )
        observations = ctx.observations + bound

        # R-mode regress is a READ of the believed oracle (ADR-0009): it confirms
        # the seeded success signals and reports a verdict; it must NOT GROW the
        # believed set. `write_observations` appends promotable ObservationEvents
        # (unlike write_candidates, which routes to the non-promotable
        # CandidateEvent stream, ADR-0014), so persisting a confirmation run makes
        # each single-agent confirmation promotable evidence. Combined with the
        # goal-level promotion flag that defect B exploited, that self-certified
        # the oracle (the create-welcome-popup inflation from 4 seeded signals to
        # 26 agent-sourced ones). The verdict is computed in-memory from
        # ctx.observations below, so persistence is not needed to reach it.
        # Default OFF for R-mode regress (ADR-0029 defect A); the verdict and the
        # RunResult still carry what the run observed. Ref-bound confirmations
        # are NEVER written here even on opt-in: a confirmation persists only to
        # the non-promotable regress record (ADR-0033 forbidden alternative; the
        # believed set never grows from a confirmation).
        if persist_observations and ctx.observations:
            self.adapter.write_observations(
                goal_id=goal_id,
                agent_id=self.agent_id,
                observations=ctx.observations,
                observed_app_version=self.observed_app_version,
            )

        verdict, matched_success, matched_failure = verdict_from_observations(
            kf, observations,
        )

        # ADR-0033 decision 7: name the believed FREE-TEXT success signals that
        # were matched by the legacy Jaccard paraphrase path rather than a
        # ref-bound confirmation, so the remaining roulette is visible in the
        # report instead of silent (the fallback lasts one transition release).
        paraphrase_matched = False
        confirmed_refs = {
            o.ref for o in observations
            if o.ref is not None and o.present and not _is_void(o)
        }
        for i, st in enumerate(kf.success_signals):
            if (st.status == Status.BELIEVED
                    and st.value in matched_success
                    and f"S{i + 1}" not in confirmed_refs
                    and st.check is None and st.value_predicate is None):
                paraphrase_matched = True
                break

        # ADR-0033 decision 5: the advisory tripwire flag messages (recorded in
        # the report and the persisted record, never changing the verdict).
        flag_messages = [
            f"{o.ref}: advisory flag {f}"
            for o in bound
            for f in (o.flags or [])
            if not f.startswith(_VOID_FLAG_PREFIX)
        ]

        # Persist the NON-PROMOTABLE regress audit record (ADR-0023 decision 4):
        # every regress run that reaches a verdict leaves a traceable record of
        # the brain's observation envelope plus the computed verdict, so a
        # REGRESSED can be told apart from a brain / observability miss after the
        # fact. This is distinct from `write_observations` above: it lands in the
        # sibling `regress/` store subdir and the merge projection NEVER reads
        # it, so it cannot grow the believed set (the ADR-0029 defect A closure
        # holds). Written for the verdicts that actually exercised the oracle
        # (PASS / FAIL / UNCERTAIN / AUTH_EXPIRED); the empty-observation case is
        # still recorded because an absent envelope is itself the evidence behind
        # an UNCERTAIN -> REGRESSED routing. Guarded so an adapter that predates
        # this SPI method (a hand-rolled test double) does not break.
        write_regress = getattr(self.adapter, "write_regress_observation", None)
        if callable(write_regress):
            # The bound confirmations (ref + system-stamped value + evidence +
            # flags) and the void reasons land in the SAME non-promotable
            # record (ADR-0033 decision 5: every confirmation auditable
            # forever; no second record kind). A test double that predates the
            # `voids` kwarg still works: fall back to the original signature.
            try:
                write_regress(
                    goal_id=goal_id,
                    agent_id=self.agent_id,
                    verdict=verdict.value,
                    observations=observations,
                    observed_app_version=self.observed_app_version,
                    voids=voids or None,
                )
            except TypeError:
                write_regress(
                    goal_id=goal_id,
                    agent_id=self.agent_id,
                    verdict=verdict.value,
                    observations=observations,
                    observed_app_version=self.observed_app_version,
                )

        return RunResult(
            goal_id=goal_id,
            verdict=verdict,
            actions=ctx.actions,
            tokens=ctx.tokens,
            wall_seconds=wall,
            observed_signals=observations,
            matched_success=matched_success,
            matched_failure=matched_failure,
            notes=ctx.notes,
            healthy_equivalent_observed=ctx.healthy_equivalent_observed,
            authenticated=ctx.authenticated,
            void_confirmations=voids,
            confirmation_flags=flag_messages,
            paraphrase_matched=paraphrase_matched,
            started_at=started_at,
            ended_at=ended_at,
        )

    def run_all(self, goal_ids: list[str], executor: Executor, *,
                 budget_actions: int | None = None,
                 budget_tokens: int | None = None,
                 stop_on_fail: bool = False) -> list[RunResult]:
        results: list[RunResult] = []
        for gid in goal_ids:
            r = self.run_one(
                gid, executor,
                budget_actions=budget_actions, budget_tokens=budget_tokens,
            )
            results.append(r)
            if stop_on_fail and r.verdict == RegressionVerdict.FAIL:
                break
        return results


# --- the aggregate (default-all) break-vs-drift run (ADR-0023) --------------


@dataclass(frozen=True)
class BudgetSlice:
    """The per-goal budget slice for an aggregate run (ADR-0023 decision 7).

    Each goal gets its OWN slice, not a shared pool the goals race for, so one
    expensive or pathological goal cannot starve the rest. A goal that exhausts
    its slice without reaching a verdict is a loud ERROR for that goal (decision
    4), never a silent skip. `tokens` and `wall_seconds` are None when that
    dimension is unbounded.
    """

    tokens: int | None = None
    wall_seconds: float | None = None


@dataclass
class GoalReport:
    """The per-goal line in an aggregate report: the break-vs-drift verdict plus
    the evidence that produced it (ADR-0023 decision 3).

    `evidence` is the named, traceable reason the verdict was chosen:
      - REGRESSED -> the believed signal(s) that flipped (absent success or
        fired failure), named so the routing to "file a bug" is concrete.
      - STALE     -> the ADR-0013 version anchor (goal version behind the live
        app) or the healthy-equivalent note that routes to "re-seed".
      - ERROR     -> the reason a verdict could not be reached (exception text,
        budget exhaustion), named so the goal is never silently dropped.
      - OK        -> empty (no follow-up needed).

    `signals` is the machine-readable list of flipped/anchoring signal values
    (a superset of what `evidence` renders), so a consumer can route without
    re-parsing prose. `result` is the underlying RunResult when one was reached
    (None for an ERROR that never produced a run, e.g. the adapter threw).
    """

    goal_id: str
    verdict: AggregateVerdict
    evidence: str
    signals: list[str] = field(default_factory=list)
    result: RunResult | None = None
    budget: BudgetSlice | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def fails_run(self) -> bool:
        return self.verdict.fails_run


def _goal_version_anchor(kf: KnowledgeFile) -> str | None:
    """The believed knowledge's anchored observed_app_version, if any.

    The projection stamps each believed success signal's
    `provenance.observed_app_version` with the version it was last verified at
    (ADR-0013). The goal's anchor is the LOWEST such version present across its
    believed success signals: that is the version the knowledge is pinned to,
    and it is the one that decays first as the live app moves ahead. Returns
    None when no believed success signal carries a version (non-semver or
    unset; the caller then has no version-anchor STALE input and falls back to
    the healthy-equivalent path).
    """
    versions = [
        s.provenance.observed_app_version
        for s in kf.success_signals
        if s.status == Status.BELIEVED and s.provenance.observed_app_version
    ]
    if not versions:
        return None
    # Keep it deterministic and conservative: the smallest (oldest) anchor is
    # the one the decay model stales against the live current_version. Order by
    # SEMVER, not raw string: lexicographic `min` would rank "1.10.0" before
    # "1.9.0" and pick the newer anchor, making the goal look less behind and
    # letting the ADR-0013 version-anchor STALE path under-fire. Reuse the same
    # `_parse_semver` the decay projection keys on so the anchor and the store
    # stay one rule. Non-semver tags sort first (they have no version-decay
    # input anyway; the caller falls back to the healthy-equivalent path).
    return min(versions, key=lambda v: _parse_semver(v) or (0, 0, 0))


def _version_anchor_is_behind(
    kf: KnowledgeFile,
    current_version: str | None,
    *,
    config: DecayConfig | None = None,
) -> tuple[bool, str | None]:
    """True when the goal's anchored version is behind the live app per the
    ADR-0013 decay anchor (more than N minors back, or a major bump).

    Returns `(behind, anchor)` where `anchor` is the goal version that was
    compared (None when there was no version anchor to compare). Reuses the
    SAME per-observation decay predicate the projection uses
    (`merge.decay.is_observation_staled`) so the STALE classification and the
    store's decay stay one rule, not two drifting copies. The wall-clock arm is
    neutralized here (now == last_verified) so this is a pure version-anchor
    check; wall-clock decay remains the projection's job.
    """
    anchor = _goal_version_anchor(kf)
    if anchor is None or current_version is None:
        return False, anchor
    cfg = config or DecayConfig()
    # Neutralize wall-clock: pass now == obs_ts so only the version arm can fire.
    fixed = datetime.now(timezone.utc)
    staled, rule = is_observation_staled(
        obs_version=anchor,
        obs_ts=fixed,
        current_version=current_version,
        now=fixed,
        config=cfg,
    )
    behind = staled and rule == "version"
    return behind, anchor


def _expected_authenticated_scope(kf: KnowledgeFile) -> str | None:
    """The authenticated role the goal expects, or None.

    A goal "expects an authenticated scope" when its ADR-0017 `auth_state` is
    present, believes the session is `authenticated`, and carries a `scope`
    that is an authenticated role (anything that is NOT `anonymous`). Returns
    that role string so the AUTH-EXPIRED evidence can name it. Returns None when
    there is no auth_state, the goal is anonymous-scoped, or auth_state does not
    claim an authenticated session: those goals are never AUTH-EXPIRED (ADR-0026
    decision 5; the anonymous-scope path is unaffected).
    """
    auth = kf.auth_state
    if auth is None or not auth.authenticated:
        return None
    scope = auth.scope
    if scope is None or scope.strip().lower() == "anonymous":
        return None
    return scope


def _confirmation_audit_suffix(result: RunResult) -> str:
    """The ADR-0033 audit suffix for a GoalReport's evidence string.

    Decision 4: the report NAMES every void confirmation and its reason, so a
    REGRESSED produced by a sloppy envelope is distinguishable from one
    produced by the app, from the run output alone. Decision 5: the advisory
    tripwire flags are recorded in the report (and the persisted record) but
    NEVER change the verdict. Decision 7: a believed free-text signal matched
    by the legacy paraphrase path is flagged so the remaining roulette is
    visible (one transition release). Empty when the run had none of these.
    """
    extras: list[str] = []
    if result.void_confirmations:
        extras.append(
            "void confirmation(s): " + "; ".join(result.void_confirmations)
        )
    if result.confirmation_flags:
        extras.append(
            "advisory flag(s), not gating: "
            + "; ".join(result.confirmation_flags)
        )
    if result.paraphrase_matched:
        extras.append(
            "matched by paraphrase: a believed free-text signal was matched "
            "by the legacy Jaccard path, not a ref-bound confirmation "
            "(legacy fallback; removed after one transition release)"
        )
    return " | ".join(extras)


def classify_goal(
    kf: KnowledgeFile,
    result: RunResult,
    *,
    current_version: str | None = None,
    decay_config: DecayConfig | None = None,
) -> GoalReport:
    """Map one goal's RunResult into the OK / REGRESSED / STALE / AUTH-EXPIRED
    aggregate verdict, carrying the evidence plus the ADR-0033 confirmation
    audit suffix (voids named, advisory flags recorded, paraphrase fallback
    flagged; none of them changes the verdict)."""
    report = _classify_goal_verdict(
        kf, result, current_version=current_version, decay_config=decay_config,
    )
    suffix = _confirmation_audit_suffix(result)
    if suffix:
        report.evidence = f"{report.evidence} | {suffix}" if report.evidence else suffix
    return report


def _classify_goal_verdict(
    kf: KnowledgeFile,
    result: RunResult,
    *,
    current_version: str | None = None,
    decay_config: DecayConfig | None = None,
) -> GoalReport:
    """Map one goal's RunResult into the OK / REGRESSED / STALE / AUTH-EXPIRED
    aggregate verdict, carrying the evidence (ADR-0023 decision 3, ADR-0026
    decision 5).

    Rules, in order:
      0. The goal expected an authenticated scope (ADR-0017 auth_state with an
         authenticated, non-anonymous role) but the run observed
         `authenticated == False` (a login wall / logged-out browser) ->
         AUTH-EXPIRED, naming the goal and the expired role. The saved session
         expired; this is NOT a regression (the app did not break) and NOT
         stale knowledge. Routed BEFORE FAIL/REGRESSED, PASS, and
         UNCERTAIN/STALE so an expired session is never mislabeled as a
         regression and never collapsed into a green OK (ADR-0026 decision 5,
         AGENTS.md loud-over-silent).
      1. The run FAILED (a failure signal fired) -> REGRESSED, naming the
         fired signal(s). The app broke (decision 3).
      2. The run PASSED (all believed success signals observed, no failure)
         -> OK.
      3. The run was UNCERTAIN (a believed success signal is absent and no
         failure fired). This is the drift-vs-break fork:
           a. The brain reported a healthy equivalent of the success path
              (`healthy_equivalent_observed`) -> STALE: the app changed on
              purpose, re-seed the knowledge.
           b. The goal's anchored version is behind the live app per the
              ADR-0013 decay anchor -> STALE: the knowledge is pinned to an
              older app version.
           c. Otherwise -> REGRESSED, naming the believed success signal(s)
              that are now absent. A missing success path with no benign
              explanation is treated as a break, not silently excused as
              drift (docs/06: loud over convenient).

    A goal that ERRORS never reaches here; the orchestrator builds its
    GoalReport directly so a thrown adapter or an exhausted budget is a loud
    ERROR, not a misclassified verdict.
    """
    expected_role = _expected_authenticated_scope(kf)
    if expected_role is not None and not result.authenticated:
        return GoalReport(
            goal_id=result.goal_id,
            verdict=AggregateVerdict.AUTH_EXPIRED,
            evidence=(
                f"the run could not authenticate as role {expected_role!r}: "
                f"a login wall / logged-out browser was observed where an "
                f"authenticated session was expected. The saved session is "
                f"expired or invalid (ADR-0026 decision 5); refresh it. This "
                f"is not a regression (the app did not break) and not stale "
                f"knowledge."
            ),
            signals=[expected_role],
            result=result,
        )

    if result.verdict == RegressionVerdict.FAIL:
        flipped = list(result.matched_failure) or ["(unspecified failure signal)"]
        return GoalReport(
            goal_id=result.goal_id,
            verdict=AggregateVerdict.REGRESSED,
            evidence="failure signal fired: " + "; ".join(flipped),
            signals=flipped,
            result=result,
        )

    if result.verdict == RegressionVerdict.PASS:
        return GoalReport(
            goal_id=result.goal_id,
            verdict=AggregateVerdict.OK,
            evidence=f"all {len(result.matched_success)} believed success signals observed",
            signals=[],
            result=result,
        )

    # UNCERTAIN: a believed success signal is absent and no failure fired.
    believed_success = [
        s.value for s in kf.success_signals if s.status == Status.BELIEVED
    ]
    absent = [v for v in believed_success if v not in set(result.matched_success)]

    if result.healthy_equivalent_observed:
        return GoalReport(
            goal_id=result.goal_id,
            verdict=AggregateVerdict.STALE,
            evidence=(
                "healthy equivalent of the success path observed; the believed "
                "knowledge is outdated (re-seed). Absent literal signal(s): "
                + "; ".join(absent or ["(none named)"])
            ),
            signals=absent,
            result=result,
        )

    behind, anchor = _version_anchor_is_behind(
        kf, current_version, config=decay_config,
    )
    if behind:
        return GoalReport(
            goal_id=result.goal_id,
            verdict=AggregateVerdict.STALE,
            evidence=(
                f"goal anchored at app version {anchor!r} is behind the live "
                f"app {current_version!r} (ADR-0013 decay anchor); the "
                f"knowledge is outdated (re-seed)"
            ),
            signals=absent,
            result=result,
        )

    return GoalReport(
        goal_id=result.goal_id,
        verdict=AggregateVerdict.REGRESSED,
        evidence=(
            "believed success signal(s) now absent: "
            + "; ".join(absent or ["(no believed success signal matched)"])
        ),
        signals=absent or believed_success,
        result=result,
    )


def _aggregate_one_goal(
    runner: "RegressionRunner",
    gid: str,
    kf: "KnowledgeFile | None",
    executor: Executor,
    slice_: BudgetSlice,
    *,
    current_version: str | None,
    budget_tokens_per_goal: int | None,
    budget_actions_per_goal: int | None,
    budget_wall_seconds_per_goal: float | None,
    decay_config: DecayConfig | None,
) -> GoalReport:
    """Compute the GoalReport for ONE goal: read-knowledge guard, run the brain
    within the per-goal slice, enforce the budget, classify.

    Returns a report for every input; it NEVER raises (a thrown brain becomes a
    loud ERROR report), so it is safe to dispatch concurrently (ADR-0027
    decision 4): a worker thread always returns a report and order stays
    recoverable.
    """
    if kf is None:
        return GoalReport(
            goal_id=gid,
            verdict=AggregateVerdict.ERROR,
            evidence=(
                "no believed knowledge for this goal (seed it with "
                "`praxis learn`); cannot reach a verdict"
            ),
            signals=[],
            budget=slice_,
        )
    try:
        result = runner.run_one(
            gid, executor,
            budget_tokens=budget_tokens_per_goal,
            budget_actions=budget_actions_per_goal,
        )
    except Exception as exc:  # noqa: BLE001 - a thrown goal must be a loud ERROR
        return GoalReport(
            goal_id=gid,
            verdict=AggregateVerdict.ERROR,
            evidence=f"could not reach a verdict: {type(exc).__name__}: {exc}",
            signals=[],
            budget=slice_,
        )

    # Per-goal budget enforcement (decision 7): a goal that exhausted its
    # token or wall-time slice is a loud ERROR, not a trusted verdict.
    exhausted: list[str] = []
    if (budget_tokens_per_goal is not None and result.tokens is not None
            and result.tokens > budget_tokens_per_goal):
        exhausted.append(
            f"tokens {result.tokens} > slice {budget_tokens_per_goal}"
        )
    if (budget_wall_seconds_per_goal is not None
            and result.wall_seconds > budget_wall_seconds_per_goal):
        exhausted.append(
            f"wall {result.wall_seconds:.2f}s > slice "
            f"{budget_wall_seconds_per_goal:.2f}s"
        )
    if exhausted:
        return GoalReport(
            goal_id=gid,
            verdict=AggregateVerdict.ERROR,
            evidence="per-goal budget exhausted: " + "; ".join(exhausted),
            signals=[],
            result=result,
            budget=slice_,
            notes=result.notes,
        )

    report = classify_goal(
        kf, result,
        current_version=current_version,
        decay_config=decay_config,
    )
    report.budget = slice_
    report.notes = result.notes
    return report


def run_aggregate(
    runner: "RegressionRunner",
    goal_ids: list[str],
    executor: Executor,
    *,
    current_version: str | None = None,
    budget_tokens_per_goal: int | None = None,
    budget_actions_per_goal: int | None = None,
    budget_wall_seconds_per_goal: float | None = None,
    decay_config: DecayConfig | None = None,
    jobs: int = 1,
    on_goal_start: "Callable[[str], None] | None" = None,
    on_goal_done: "Callable[[GoalReport], None] | None" = None,
) -> list[GoalReport]:
    """Run R-mode across every goal with a PER-GOAL budget slice and emit one
    GoalReport per goal (ADR-0023 decisions 2, 3, 4, 7; ADR-0027 decision 4).

    Every goal is attempted within its OWN budget slice (decision 7); a goal
    that throws or exhausts its slice without a verdict becomes a loud ERROR for
    that goal (decision 4), never silently skipped and never counted OK. The
    order of `goal_ids` is preserved so the report is stable across runs.

    The wall-time slice is enforced as a post-hoc cap: the executor is a single
    opaque call the runner cannot interrupt mid-flight, so a goal whose run
    exceeds its wall slice is reported as an ERROR (budget exhausted) rather
    than its verdict being trusted. The token slice is passed into the prompt
    budget; the same post-hoc cap turns an over-token run into an ERROR.

    `jobs` caps how many goals run concurrently (ADR-0027 decision 4): the
    default 1 is strictly sequential (unchanged behavior); `jobs > 1` runs
    feature / precondition goals in a bounded thread pool while auth-SUBJECT
    goals (`auth_state.being_tested`) run serially so two real logins never
    collide on one test account. The per-goal budget and the loud-ERROR contract
    are unchanged by concurrency; only the scheduling differs. `on_goal_done`
    fires once per completed goal in the calling thread (a progress callback);
    `on_goal_start` fires once per goal just before it runs, naming the goal a
    live single-line progress display shows as currently running.
    """
    slice_ = BudgetSlice(
        tokens=budget_tokens_per_goal,
        wall_seconds=budget_wall_seconds_per_goal,
    )
    # Read each goal's believed knowledge once, up front: it is the auth-subject
    # partition input (ADR-0027 decision 4) and the per-goal body's input, so a
    # single read serves both and the concurrent workers do not re-read.
    kfs = {gid: runner.adapter.read_knowledge(gid) for gid in goal_ids}

    def _one(gid: str) -> GoalReport:
        return _aggregate_one_goal(
            runner, gid, kfs[gid], executor, slice_,
            current_version=current_version,
            budget_tokens_per_goal=budget_tokens_per_goal,
            budget_actions_per_goal=budget_actions_per_goal,
            budget_wall_seconds_per_goal=budget_wall_seconds_per_goal,
            decay_config=decay_config,
        )

    def _is_subject(gid: str) -> bool:
        kf = kfs[gid]
        return kf is not None and kf.auth_state is not None \
            and kf.auth_state.being_tested

    return run_partitioned(
        goal_ids, _one, is_subject=_is_subject, jobs=jobs,
        on_start=on_goal_start, on_done=on_goal_done,
    )


def aggregate_failed(reports: list[GoalReport]) -> bool:
    """True when any goal REGRESSED or ERRORED (ADR-0023 decision 4).

    One non-OK goal fails the whole run; a "mostly green" roll-up never buries a
    single regression. STALE does NOT fail the run (the app changed on purpose;
    the fix is a human re-seed, not a red gate). Used by both surfaces to
    compute the exit code / skill triage decision in one place.
    """
    return any(r.fails_run for r in reports)
