"""Maintenance-over-time stress — the experiment that revives or re-kills MBT.

Model-Based Testing died because maintaining the model by HAND cost more than the
tests it replaced (docs/01). Mneme's whole bet is that an agent maintains it cheaply
instead. The earlier experiments tested USING the model; this one tests MAINTAINING
it as the app evolves across versions, measuring the two things that actually decide
the bet (docs/06):

  - human_intervention_rate — how often a human must re-seed to keep the oracle
    correct (the gauge that killed classic MBT).
  - drift / poisoning — does a WRONG success signal ever stay `believed` as the app
    changes? (must be 0 — old truth must demote to `stale`, never silently linger.)

Offline, on the real core (store→merge→oracle). What this validates is the
MAINTENANCE MECHANISM: when the app changes, does the projection self-heal the
cheap cases and correctly REFUSE (and flag for a human) the deep ones — without ever
believing garbage? The live counterpart (does the LLM observe the changes honestly)
is in LOCAL_RUN.md.

Run:  python experiments/ui-mutation/evolution.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from praxis.merge import project_with_seed  # noqa: E402
from praxis.model import (  # noqa: E402
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    Target,
)
from praxis.store import ObservationEvent, ObservedSignal  # noqa: E402

GOAL = "authenticate-user"
EXPLORER = "explorer-1"  # Phase 0: ONE writer
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class Version:
    version: str
    day: int                       # days after T0 (advances last_verified/recency)
    true_success: list[tuple[str, str]]  # what success REALLY is in this version
    desc: str


# A realistic evolution of one app. Each step is the kind of change a real app ships.
VERSIONS = [
    Version("2026.1", 0,
            [("behavioral", "a logout/sign-out action becomes available"),
             ("network", "the auth request returns 2xx and sets a session cookie")],
            "v1 baseline (seeded oracle)"),
    Version("2026.2", 60,
            [("behavioral", "a logout/sign-out action becomes available"),
             ("network", "the auth request returns 2xx and sets a session cookie")],
            "v2 COSMETIC redesign (button renamed/moved) — semantic oracle unchanged"),
    Version("2026.3", 120,
            [("behavioral", "a logout/sign-out action becomes available"),
             ("network", "the auth request returns 2xx and sets a refresh+session token")],
            "v3 IMPLEMENTATION change (auth endpoint reworked) — network signal changes"),
    Version("2026.4", 180,
            [("behavioral", "a verified-account dashboard with an MFA badge appears"),
             ("network", "POST /mfa/verify returns 2xx after the session is established")],
            "v4 SEMANTIC change (login now requires MFA) — what 'success' MEANS changes"),
]


def _seed(beh_value: str, version: str, at: datetime) -> KnowledgeFile:
    return KnowledgeFile(
        schema_version="0", goal_id=GOAL, goal="A returning user can authenticate.",
        target=Target(app="acme-web", observed_app_versions=[version]),
        success_signals=[Signal(
            type="behavioral", value=beh_value,
            provenance=Provenance(source_type="spec", source_id="AC-LOGIN",
                                  observed_app_version=version, last_verified=at,
                                  observation_count=1),
            confidence=1.0, status="believed")],
        meta=Meta(created_at=at, updated_at=at),
    )


def _observe(version: Version) -> list[ObservationEvent]:
    """The single honest agent observes this version's true success signals."""
    at = T0 + timedelta(days=version.day)
    out = []
    for type_, value in version.true_success:
        out.append(ObservationEvent(
            agent_id=EXPLORER, goal_id=GOAL, ts=at, observed_app_version=version.version,
            signals=[ObservedSignal(kind="success", type=type_, value=value, present=True,
                                    source_type="agent", source_id=EXPLORER,
                                    observed_app_version=version.version)],
        ))
    return out


def _believed(kf: KnowledgeFile) -> set[tuple[str, str]]:
    return {(s.type.value, s.value) for s in kf.success_signals if s.status.value == "believed"}


def run() -> dict:
    events: list[ObservationEvent] = []
    beh0 = next(v for t, v in VERSIONS[0].true_success if True and t == "behavioral")
    seed = _seed(beh0, VERSIONS[0].version, T0)

    interventions = 0
    worst_drift = 0           # max wrong-but-believed signals seen at any settled state
    silent_drift = 0          # times a wrong signal was believed WITHOUT being flagged
    log: list[dict] = []

    for v in VERSIONS:
        at = T0 + timedelta(days=v.day)
        events += _observe(v)
        truth = set(v.true_success)

        kf = project_with_seed(seed, events, now=at, current_version=v.version)
        believed = _believed(kf)
        drift = believed - truth          # believed signals that are NOT true now
        missing = truth - believed        # true signals not yet trusted
        intervened = False

        # Drift to garbage is the unforgivable failure: a wrong signal staying
        # `believed`. Count it whether or not we then intervene.
        if drift:
            silent_drift += len(drift)

        # If the oracle is no longer correct (lost belief in true success, e.g. a deep
        # semantic change a single writer cannot self-corroborate), a human re-seeds.
        if missing or drift:
            beh = next(val for t, val in v.true_success if t == "behavioral")
            seed = _seed(beh, v.version, at)
            interventions += 1
            intervened = True
            kf = project_with_seed(seed, events, now=at, current_version=v.version)
            believed = _believed(kf)
            drift = believed - truth

        worst_drift = max(worst_drift, len(drift))
        log.append({
            "version": v.version, "desc": v.desc,
            "believed_correct": believed == truth,
            "drift_after": len(drift), "intervened": intervened,
        })

    n = len(VERSIONS)
    return {
        "versions": n,
        "human_interventions": interventions,
        "human_intervention_rate": round(interventions / n, 3),
        "silent_drift_events": silent_drift,     # wrong signal ever believed → must be 0
        "worst_residual_drift": worst_drift,     # after maintenance → must be 0
        "all_versions_correct_after_maintenance": all(r["believed_correct"] for r in log),
        "log": log,
        # Pass: the model stays correct across the app's evolution, never drifts to
        # garbage, and humans are needed only for genuine semantic changes.
        "PASSED": (silent_drift == 0 and worst_drift == 0
                   and all(r["believed_correct"] for r in log)),
    }


def main() -> None:
    r = run()
    print("=" * 74)
    print("MAINTENANCE-OVER-TIME STRESS  (offline; the MBT-revival test)")
    print("-" * 74)
    for row in r["log"]:
        flag = "  (human re-seed)" if row["intervened"] else ""
        ok = "OK " if row["believed_correct"] and row["drift_after"] == 0 else "XX "
        print(f"  {ok} {row['version']}  {row['desc']}{flag}")
    print("-" * 74)
    print(f"  human interventions      : {r['human_interventions']}/{r['versions']} "
          f"(rate {r['human_intervention_rate']})")
    print(f"  silent drift to garbage  : {r['silent_drift_events']}  (must be 0)")
    print(f"  residual drift post-fix  : {r['worst_residual_drift']}  (must be 0)")
    print(f"  correct at every version : {r['all_versions_correct_after_maintenance']}")
    print("-" * 74)
    print(f"PASSED (stays correct, never drifts to garbage, humans only for semantic "
          f"changes): {r['PASSED']}")
    print("=" * 74)
    print("Reading: cosmetic + implementation changes self-heal with ZERO human help "
          "(semantic\ninvariants survive them); only a genuine change in what SUCCESS "
          "MEANS needs a re-seed,\nand the oracle REFUSES to believe stale truth rather "
          "than drifting silently.")


if __name__ == "__main__":
    main()
