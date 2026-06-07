"""Adversarial stress for the oracle — the failure mode that actually kills the
product (docs/06): silent poisoning, where a confidently-wrong success signal
becomes `believed` and tests start passing while the app is broken.

This runs entirely on the runtime-agnostic core (store→merge→oracle), so it is
deterministic, free, and exhaustive — no browser, no LLM. We inject poisoned event
streams and check whether any UNSUPPORTED success signal reaches `believed`
("false belief"), and whether the run-level oracle ever fires while ground truth is
failure ("false pass").

Two scenario classes:
  RESIST  — cases ADR-0005 is meant to defeat; a breach here is a real bug.
  PROBE   — deeper attacks that test the SPIRIT of the rule; a breach here is a
            documented gap that informs Phase-1 hardening (not necessarily a bug
            in today's stated rule).

Run:  python experiments/ui-mutation/oracle_stress.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mneme.merge import project  # noqa: E402
from mneme.model import Target  # noqa: E402
from mneme.store import ObservationEvent, ObservedSignal  # noqa: E402

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


# --------------------------------------------------------------------------- PROBE

def s_single_source_two_types() -> int:
    """THE deep attack: ONE agent fabricates TWO different evidence types. The
    stated rule counts type-diversity regardless of source, so this is promoted to
    believed even though a single (possibly hallucinating) source produced both.
    Type-diversity without SOURCE-independence is a poisoning vector."""
    events = [_ev("a1", "behavioral", POISON), _ev("a1", "network", POISON2)]
    return len(_believed_values(events) & {POISON, POISON2})


def s_seed_rides_single_agent() -> int:
    """A correct seed (behavioral) + a SINGLE agent asserting a different-type poison
    (network). The seed supplies the 'diversity', so the lone agent's fabricated
    network signal rides to believed."""
    seed = [ObservedSignal(kind="success", type="behavioral",
                           value="real: authenticated home reachable (AC)", present=True,
                           source_type="spec", source_id="AC-1", observed_app_version=CUR,
                           confidence=1.0)]
    events = [_ev("a1", "network", POISON)]
    return len(_believed_values(events, seeded=seed) & {POISON})


def run() -> dict:
    resist: list[tuple[str, int]] = [
        ("lone_single_type", s_lone_single_type()),
        ("contradiction", s_contradiction()),
        ("oscillation", s_oscillation()),
        ("stale_demotion", s_stale()),
    ]
    for k in (2, 5, 20, 100):
        resist.append((f"correlated_same_type x{k}", s_correlated_same_type(k)))

    control_believed = s_positive_control()
    probes: list[tuple[str, int]] = [
        ("single_source_two_types", s_single_source_two_types()),
        ("seed_rides_single_agent", s_seed_rides_single_agent()),
    ]

    resist_breaches = [name for name, fb in resist if fb > 0]
    probe_breaches = [name for name, fb in probes if fb > 0]
    return {
        "resist": resist,
        "resist_breaches": resist_breaches,
        "positive_control_believed": control_believed,
        "probes": probes,
        "probe_breaches": probe_breaches,
        # Core verdict: ADR-0005's targeted attacks are all resisted AND real
        # evidence is still accepted. Probe breaches are reported separately as
        # Phase-1 hardening signals, not core failures.
        "CORE_PASSED": (not resist_breaches) and control_believed >= 1,
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
    print("-" * 72)
    print("PROBE scenarios (test the SPIRIT of diversity; breaches = Phase-1 gaps):")
    for name, fb in r["probes"]:
        print(f"    {'-- ' if fb == 0 else 'GAP'} {name:28} false_beliefs={fb}")
    print("-" * 72)
    print(f"CORE_PASSED (ADR-0005 attacks resisted + real evidence accepted): "
          f"{r['CORE_PASSED']}")
    if r["probe_breaches"]:
        print("KNOWN GAPS (Phase-1 hardening — type-diversity needs source-independence):")
        for name in r["probe_breaches"]:
            print(f"    - {name}")
    print("=" * 72)


if __name__ == "__main__":
    main()
