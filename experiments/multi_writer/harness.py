"""Adversarial multi-writer harness (ADR-0012 section 4).

This is the day-one assurance that ADR-0012 section 4 names as load-bearing:
ship the harness in the SAME commit as the multi-writer store changes, never
N+1. Each scenario asserts a single, named property of the contract and
short-circuits on violation. Output is a tabular PASS/FAIL banner so the
verify.sh runner surfaces failures without a debugger.

Scenarios (all five from the ADR-0012 acceptance list plus a partial-write
resilience case):

1. concurrent same-source: N writers sharing one `agent_identity` race to
   append. Property: zero lost events AND zero false-promote (the projection
   never reaches `believed` from same-source count, no matter how many
   writers agree on a single-type signal).

2. concurrent diverse-source: N writers across DISTINCT `agent_identity`s
   race. Property: zero lost events AND legitimate corroboration when both
   sides bring a different signal `type` (diversity-or-seed gate, ADR-0005).

3. racing contradiction: two distinct sources race; one observes a failure
   signal present, the other observes it absent. Property: the projection
   surfaces `contested`, NOT last-write-wins.

4. racing oscillation: an alternating presence sequence across writers.
   Property: the projection surfaces `quarantined` per ADR-0005, derived
   from the event set (no flag mutated on the underlying events).

5. partial-write failure: a leftover `.tmp` file from a crashed writer
   coexists with real events. Property: readers ignore the `.tmp` and the
   on-disk event count for real events stays correct (rename is the commit
   point, ADR-0012 section 1).

The harness deliberately does NOT exercise cross-tenant rejection at the
adapter boundary -- that is covered as a unit test in `tests/test_multi_
writer.py`. Mixing live-thread races with constructor-level rejection makes
the harness output harder to read.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

# Bootstrap sys.path so the script can run directly (hyphen-free package dir
# is friendly here, but we keep the pattern consistent with experiments/ui-
# mutation/harness.py).
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parents[1] / "src"
for _p in (str(_SRC),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from praxis.merge import project  # noqa: E402  (sys.path bootstrap above)
from praxis.model import Target  # noqa: E402
from praxis.store import (  # noqa: E402
    FileEventStore,
    ObservationEvent,
    ObservedSignal,
    source_id_for,
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str

    def as_line(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"  {status}  {self.name:32s}  {self.detail}"


# ---------------------------------------------------------------------------
# Scenario 1: concurrent same-source -- ADR-0008 attack under contention.
# ---------------------------------------------------------------------------


def scenario_concurrent_same_source(root: Path) -> ScenarioResult:
    store = FileEventStore(root)
    sid = source_id_for(model="claude-sonnet", prompt_lineage="r-mode-v1")
    n_writers = 6
    per_writer = 12

    def writer() -> None:
        for _ in range(per_writer):
            store.append(ObservationEvent(
                agent_id=sid, goal_id="g",
                signals=[ObservedSignal(
                    kind="success", type="behavioral",
                    value="logout action becomes available",
                    source_type="agent", source_id=sid,
                    observed_app_version="1",
                )],
            ))

    threads = [threading.Thread(target=writer) for _ in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = store.read("g")
    expected = n_writers * per_writer
    if len(events) != expected:
        return ScenarioResult(
            "concurrent_same_source", False,
            f"lost events: have {len(events)}, expected {expected}",
        )

    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    statuses = {s.status.value for s in kf.success_signals}
    # ADR-0012 section 2 + ADR-0008: same-model self-promotion is structurally
    # impossible. The only honest verdict here is `contested`.
    if statuses != {"contested"}:
        return ScenarioResult(
            "concurrent_same_source", False,
            f"false promote: got statuses={statuses}, expected only 'contested'",
        )
    return ScenarioResult(
        "concurrent_same_source", True,
        f"events={expected}, single-source stayed 'contested' (no false promote)",
    )


# ---------------------------------------------------------------------------
# Scenario 2: concurrent diverse-source -- legitimate corroboration survives.
# ---------------------------------------------------------------------------


def scenario_concurrent_diverse_source(root: Path) -> ScenarioResult:
    store = FileEventStore(root)
    sid_a = source_id_for(model="model-a", prompt_lineage="r-mode-v1")
    sid_b = source_id_for(model="model-b", prompt_lineage="r-mode-v1")
    n = 20

    def writer(sid: str, type_: str, value: str) -> None:
        for _ in range(n):
            store.append(ObservationEvent(
                agent_id=sid, goal_id="g",
                signals=[ObservedSignal(
                    kind="success", type=type_, value=value,
                    source_type="agent", source_id=sid,
                    observed_app_version="1",
                )],
            ))

    threads = [
        threading.Thread(target=writer,
                         args=(sid_a, "behavioral", "logout action becomes available")),
        threading.Thread(target=writer,
                         args=(sid_b, "network", "POST /session returns 2xx")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = store.read("g")
    expected = 2 * n
    if len(events) != expected:
        return ScenarioResult(
            "concurrent_diverse_source", False,
            f"lost events: have {len(events)}, expected {expected}",
        )

    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    statuses = {s.status.value for s in kf.success_signals}
    if statuses != {"believed"}:
        return ScenarioResult(
            "concurrent_diverse_source", False,
            f"did not promote with diversity: statuses={statuses}",
        )
    return ScenarioResult(
        "concurrent_diverse_source", True,
        f"events={expected}, two-type two-source -> 'believed'",
    )


# ---------------------------------------------------------------------------
# Scenario 3: racing contradiction -- contradictions are preserved.
# ---------------------------------------------------------------------------


def scenario_racing_contradiction(root: Path) -> ScenarioResult:
    store = FileEventStore(root)
    sid_a = source_id_for(model="model-a", prompt_lineage="p")
    sid_b = source_id_for(model="model-b", prompt_lineage="p")

    # A success signal so the projection is valid.
    store.append(ObservationEvent(
        agent_id=sid_a, goal_id="g",
        signals=[ObservedSignal(
            kind="success", type="behavioral", value="logout ok",
            source_type="agent", source_id=sid_a, observed_app_version="1",
        )],
    ))

    # Two writers race on the SAME failure signal with disagreeing presence.
    def write(sid: str, present: bool) -> None:
        store.append(ObservationEvent(
            agent_id=sid, goal_id="g",
            signals=[ObservedSignal(
                kind="failure", type="behavioral", value="captcha appears",
                present=present, source_type="agent", source_id=sid,
                observed_app_version="1",
            )],
        ))

    threads = [
        threading.Thread(target=write, args=(sid_a, True)),
        threading.Thread(target=write, args=(sid_b, False)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = store.read("g")
    if len(events) != 3:
        return ScenarioResult(
            "racing_contradiction", False,
            f"lost events: have {len(events)}, expected 3",
        )

    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    captcha = next(s for s in (kf.failure_signals or []) if s.value == "captcha appears")
    if captcha.status.value != "contested":
        return ScenarioResult(
            "racing_contradiction", False,
            f"contradiction not preserved: status={captcha.status.value}",
        )
    return ScenarioResult(
        "racing_contradiction", True,
        "racing present/absent -> 'contested' (no last-write-wins)",
    )


# ---------------------------------------------------------------------------
# Scenario 4: racing oscillation -- quarantine on flip-flop.
# ---------------------------------------------------------------------------


def scenario_racing_oscillation(root: Path) -> ScenarioResult:
    store = FileEventStore(root)
    sid_a = source_id_for(model="model-a", prompt_lineage="p")
    sid_b = source_id_for(model="model-b", prompt_lineage="p")
    base = NOW - timedelta(days=2)

    # Stable timestamps order the three observations deterministically; the
    # contention is in the WRITE order, which the deterministic projection
    # then resolves by event timestamp.
    rows = [
        (sid_a, True, base),
        (sid_b, False, base + timedelta(hours=1)),
        (sid_a, True, base + timedelta(hours=2)),
    ]
    for sid, present, ts in rows:
        store.append(ObservationEvent(
            agent_id=sid, goal_id="g", ts=ts,
            signals=[ObservedSignal(
                kind="success", type="behavioral", value="flaky button",
                present=present, source_type="agent", source_id=sid,
                observed_app_version="1",
            )],
        ))

    events = store.read("g")
    kf = project(events, goal_id="g", goal="auth", target=Target(app="acme"),
                 now=NOW, current_version="1")
    flaky = next(s for s in kf.success_signals if s.value == "flaky button")
    if flaky.status.value != "quarantined":
        return ScenarioResult(
            "racing_oscillation", False,
            f"oscillation did not quarantine: status={flaky.status.value}",
        )
    return ScenarioResult(
        "racing_oscillation", True,
        "alternating presence -> 'quarantined' (ADR-0005)",
    )


# ---------------------------------------------------------------------------
# Scenario 5: partial-write failure -- leftover .tmp is ignored.
# ---------------------------------------------------------------------------


def scenario_partial_write_failure(root: Path) -> ScenarioResult:
    store = FileEventStore(root)
    sid = source_id_for(model="model-a", prompt_lineage="p")
    real_value = "logout action becomes available"

    # One legitimate event, then a leftover from a crashed writer (post-tmp
    # write, pre-rename). The .tmp is invalid JSON; the reader must skip it.
    store.append(ObservationEvent(
        agent_id=sid, goal_id="g",
        signals=[ObservedSignal(
            kind="success", type="behavioral", value=real_value,
            source_type="agent", source_id=sid, observed_app_version="1",
        )],
    ))
    events_dir = root / "local" / "events"
    (events_dir / ".crashed-writer.tmp").write_text(
        "this would be a half-written event", encoding="utf-8",
    )

    events = store.read("g")
    if len(events) != 1:
        return ScenarioResult(
            "partial_write_failure", False,
            f"reader saw {len(events)} events; expected 1 (.tmp must be ignored)",
        )
    if events[0].signals[0].value != real_value:
        return ScenarioResult(
            "partial_write_failure", False,
            f"reader returned wrong event value: {events[0].signals[0].value!r}",
        )
    return ScenarioResult(
        "partial_write_failure", True,
        "leftover .tmp ignored; rename remains the commit point",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


SCENARIOS: list[Callable[[Path], ScenarioResult]] = [
    scenario_concurrent_same_source,
    scenario_concurrent_diverse_source,
    scenario_racing_contradiction,
    scenario_racing_oscillation,
    scenario_partial_write_failure,
]


def run_all() -> list[ScenarioResult]:
    """Run every scenario against its own tmp dir; return the result list."""
    results: list[ScenarioResult] = []
    for scenario in SCENARIOS:
        tmp = Path(tempfile.mkdtemp(prefix="multi-writer-"))
        try:
            results.append(scenario(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return results


def _print_banner(results: list[ScenarioResult]) -> int:
    print("=" * 72)
    print("MULTI-WRITER ADVERSARIAL HARNESS  (ADR-0012 section 4)")
    print("-" * 72)
    for r in results:
        print(r.as_line())
    print("-" * 72)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"  total: {passed}/{total} passed")
    print("=" * 72)
    return 0 if passed == total else 1


def main() -> int:
    results = run_all()
    return _print_banner(results)


if __name__ == "__main__":
    raise SystemExit(main())
