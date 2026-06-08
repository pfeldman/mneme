"""Adversarial stress for the oracle — the failure mode that actually kills the
product (docs/06): silent poisoning, where a confidently-wrong success signal
becomes `believed` and tests start passing while the app is broken.

This runs entirely on the runtime-agnostic core (store→merge→oracle), so it is
deterministic, free, and exhaustive — no browser, no LLM. We inject poisoned event
streams and check whether any UNSUPPORTED success signal reaches `believed`
("false belief"), and whether the run-level oracle ever fires while ground truth is
failure ("false pass").

Two scenario classes:
  RESIST   — must never produce a false belief (incl. single-source self-corroboration,
             closed by the source-independence rule of ADR-0008).
  INHERENT — a seed + a single different-type agent signal IS promoted to believed.
             This is INDISTINGUISHABLE from legitimate cold-start corroboration (the
             login example), so it is expected, not a bug: the oracle cannot tell an
             honest single observation from a fabricated one. The mitigation is
             temporal (contradiction → contested, oscillation → quarantined), not at
             promotion time. Reported for transparency, never counted as a breach.

Run:  python experiments/ui-mutation/oracle_stress.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from praxis.merge import project  # noqa: E402
from praxis.model import Target  # noqa: E402
from praxis.store import ObservationEvent, ObservedSignal  # noqa: E402

NOW = datetime(2026, 6, 7, tzinfo=timezone.utc)
CUR = "2.0"
POISON = "FAKE success: app reports OK while it is actually broken"
POISON2 = "FAKE success: a second fabricated indicator"
GOAL = "authenticate-user"
TARGET = Target(app="acme", observed_app_versions=[CUR])


def _ev(agent: str, type_: str, value: str, *, present: bool = True, ver: str = CUR,
        ts: datetime | None = None, kind: str = "success") -> ObservationEvent:
    return ObservationEvent(
        agent_id=agent, goal_id=GOAL, ts=ts or NOW, observed_app_version=ver,
        signals=[ObservedSignal(kind=kind, type=type_, value=value, present=present,
                                source_type="agent", source_id=agent, observed_app_version=ver)],
    )


def _believed_values(events: list[ObservationEvent], **kw: object) -> set[str]:
    kf = project(events, goal_id=GOAL, goal="auth", target=TARGET, now=NOW,
                 current_version=CUR, **kw)  # type: ignore[arg-type]
    return {s.value for s in kf.success_signals if s.status.value == "believed"}


# --------------------------------------------------------------------------- RESIST

def s_lone_single_type() -> int:
    """One agent asserts a poison success signal. No diversity, no seed → must stay
    `contested`, never believed."""
    return len(_believed_values([_ev("a1", "behavioral", POISON)]) & {POISON})


def s_correlated_same_type(k: int) -> int:
    """k agents all assert the SAME poison signal of the SAME type. Agent count is
    not independence (ADR-0005) → must never be believed, for any k."""
    events = [_ev(f"a{i}", "network", POISON) for i in range(k)]
    return len(_believed_values(events) & {POISON})


def s_contradiction() -> int:
    """One agent sees it, another explicitly does not → contested, not believed."""
    events = [_ev("a1", "behavioral", POISON, present=True, ts=NOW - timedelta(days=1)),
              _ev("a2", "behavioral", POISON, present=False, ts=NOW)]
    return len(_believed_values(events) & {POISON})


def s_oscillation() -> int:
    """Presence flips across runs → quarantined, never believed."""
    events = [_ev("a1", "behavioral", POISON, present=True, ts=NOW - timedelta(days=2)),
              _ev("a1", "behavioral", POISON, present=False, ts=NOW - timedelta(days=1)),
              _ev("a1", "behavioral", POISON, present=True, ts=NOW)]
    return len(_believed_values(events) & {POISON})


def s_stale() -> int:
    """A diverse-but-old poison, last seen under an old version long ago, must be
    demoted to `stale` under the current version — not believed."""
    old = NOW - timedelta(days=200)
    events = [_ev("a1", "behavioral", POISON, ver="1.0", ts=old),
              _ev("a2", "network", POISON, ver="1.0", ts=old)]
    return len(_believed_values(events) & {POISON})


def s_positive_control() -> int:
    """Genuine corroboration: TWO different sources, TWO different evidence types,
    fresh. This SHOULD be believed — an oracle that refuses real evidence is
    useless. Returns count of believed (expected >=1)."""
    events = [_ev("a1", "behavioral", "real: a logout control appears"),
              _ev("a2", "network", "real: POST /session returns 2xx + cookie")]
    return len(_believed_values(events))


def s_single_source_two_types() -> int:
    """THE deep attack: ONE source fabricates TWO different evidence types and tries
    to self-corroborate. ADR-0008 source-independence requires the diverse types to
    span >=2 distinct sources, so a single source can no longer promote itself →
    must be 0."""
    events = [_ev("a1", "behavioral", POISON), _ev("a1", "network", POISON2)]
    return len(_believed_values(events) & {POISON, POISON2})


def s_seed_plus_paraphrase_stream(k: int = 26) -> int:
    """ADR-0029: a correct seed of ONE type plus a STREAM of k DISTINCT single-agent
    paraphrases of the SAME type as the seed. This is the `create-welcome-popup`
    self-pollution: regress kept minting single-agent confirmations and each one rode
    the GOAL-LEVEL independence flag (the seed sets it) to `believed`, inflating the
    believed set from the seed to k+1 entries. After the per-signal fix, no paraphrase
    has a different-type partner from a different source, so NONE is corroborated; the
    believed set stays the SEED ONLY. Returns the count of believed agent paraphrases
    (must be 0). The seed itself stays believed (verified separately below)."""
    seed = [ObservedSignal(kind="success", type="behavioral",
                           value="real: welcome popup is shown on first visit (AC)",
                           present=True, source_type="spec", source_id="AC-1",
                           observed_app_version=CUR, confidence=1.0)]
    # Each paraphrase is a DISTINCT value of the SAME (behavioral) type from a DISTINCT
    # agent: a stream of single-agent self-restatements, exactly what regress emitted.
    paraphrases = [f"FAKE paraphrase #{i}: the welcome popup appears" for i in range(k)]
    events = [_ev(f"a{i}", "behavioral", paraphrases[i]) for i in range(k)]
    believed = _believed_values(events, seeded=seed)
    return len(believed & set(paraphrases))


def s_seed_survives_paraphrase_stream() -> int:
    """Companion to the above: the SEED must stay believed under the paraphrase stream
    (the fix must not over-correct into refusing the seed). Returns 1 when the seed is
    still the believed oracle, 0 if the fix wrongly demoted it."""
    seed_value = "real: welcome popup is shown on first visit (AC)"
    seed = [ObservedSignal(kind="success", type="behavioral", value=seed_value,
                           present=True, source_type="spec", source_id="AC-1",
                           observed_app_version=CUR, confidence=1.0)]
    events = [_ev(f"a{i}", "behavioral", f"FAKE paraphrase #{i}: the welcome popup appears")
              for i in range(26)]
    return 1 if seed_value in _believed_values(events, seeded=seed) else 0


# --------------------------------------------------------------------------- INHERENT

def s_seed_rides_single_agent() -> int:
    """A correct seed (behavioral) + a SINGLE agent asserting a different-type signal
    (network). This IS promoted to believed — and it is structurally identical to the
    legitimate cold-start corroboration pattern (seed of one type + an agent of
    another type, the login example). The oracle cannot distinguish an honest from a
    fabricated single observation, so this is the inherent trust boundary, not a
    fixable gap. Mitigated temporally (contradiction/flip-flop), not at promotion."""
    seed = [ObservedSignal(kind="success", type="behavioral",
                           value="real: authenticated home reachable (AC)", present=True,
                           source_type="spec", source_id="AC-1", observed_app_version=CUR,
                           confidence=1.0)]
    events = [_ev("a1", "network", POISON)]
    return len(_believed_values(events, seeded=seed) & {POISON})


def run() -> dict:
    resist: list[tuple[str, int]] = [
        ("lone_single_type", s_lone_single_type()),
        ("single_source_two_types", s_single_source_two_types()),
        ("contradiction", s_contradiction()),
        ("oscillation", s_oscillation()),
        ("stale_demotion", s_stale()),
        ("seed_plus_paraphrase_stream", s_seed_plus_paraphrase_stream()),
    ]
    for k in (2, 5, 20, 100):
        resist.append((f"correlated_same_type x{k}", s_correlated_same_type(k)))

    control_believed = s_positive_control()
    seed_survives = s_seed_survives_paraphrase_stream()
    inherent = [("seed_rides_single_agent", s_seed_rides_single_agent())]

    resist_breaches = [name for name, fb in resist if fb > 0]
    return {
        "resist": resist,
        "resist_breaches": resist_breaches,
        "positive_control_believed": control_believed,
        "seed_survives_paraphrase_stream": seed_survives,
        "inherent": inherent,
        # All poisoning attacks resisted AND genuine evidence still accepted AND the
        # seed survives the paraphrase stream (the fix must not over-correct).
        "PASSED": (not resist_breaches) and control_believed >= 1
        and seed_survives == 1,
    }


def main() -> None:
    r = run()
    print("=" * 72)
    print("ORACLE ADVERSARIAL STRESS  (offline; core store→merge→oracle, no LLM)")
    print("-" * 72)
    print("RESIST scenarios (false beliefs must be 0):")
    for name, fb in r["resist"]:
        print(f"    {'OK ' if fb == 0 else 'XX '} {name:28} false_beliefs={fb}")
    print(f"Positive control (must be >=1 believed): {r['positive_control_believed']}")
    print(f"Seed survives paraphrase stream (must be 1): "
          f"{r['seed_survives_paraphrase_stream']}")
    print("-" * 72)
    print("INHERENT (seed + single different-type agent → believed; = legitimate")
    print("cold-start corroboration, indistinguishable from honest; mitigated over time):")
    for name, fb in r["inherent"]:
        print(f"    .. {name:28} believed={fb}")
    print("-" * 72)
    print(f"PASSED (all poisoning attacks resisted + real evidence accepted): "
          f"{r['PASSED']}")
    print("=" * 72)


if __name__ == "__main__":
    main()
