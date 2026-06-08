"""Wires the three experiment arms through the REAL core (store → merge → oracle →
adapter). Only the SUT and the agent are simulated (`simapp`); everything that the
thesis is actually about — append-only events, the believed projection, and the
diversity-or-seed oracle — is the production code path.

Arms:
  memory          — reads believed knowledge (seeded + agent observations) and
                    goal-seeks; the shared oracle decides success.
  cold_agent      — no memory; re-derives everything each run (more tokens).
  recorded_script — the brittle Playwright baseline (breaks on mutation).
"""
from __future__ import annotations

from datetime import datetime, timezone

from praxis.adapters import BrowserUseAdapter
from praxis.model import (
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    Target,
)
from praxis.store import FileEventStore, ObservedSignal

try:
    from . import simapp
except ImportError:  # plain-script execution
    import simapp  # type: ignore[no-redef]

APP_VERSION = "2026.5.3"
EXPLORER = "explorer-1"


def seed_for(flow_name: str) -> KnowledgeFile:
    """A human/spec-seeded success oracle, authored BEFORE any exploration
    (ADR-0005). The first oracle is seeded, never self-certified. We seed the
    behavioral acceptance criterion; agents add corroborating evidence types."""
    flow = simapp.base_flow(flow_name)
    behavioral = next(s for s in flow.success_signals if s.type == "behavioral")
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    seed_signal = Signal(
        type="behavioral",
        value=behavioral.value,
        provenance=Provenance(
            source_type="spec",
            source_id=f"AC-{flow.goal_id.upper()}",
            observed_app_version=APP_VERSION,
            last_verified=now,
            observation_count=1,
        ),
        confidence=1.0,
        status="believed",
    )
    return KnowledgeFile(
        schema_version="0",
        goal_id=flow.goal_id,
        goal=flow.goal,
        target=Target(app=flow.app, environment="staging", observed_app_versions=[APP_VERSION]),
        success_signals=[seed_signal],
        meta=Meta(created_at=now, updated_at=now, contributing_agents=[]),
    )


def make_adapter(store: FileEventStore, app: str) -> tuple[BrowserUseAdapter, dict[str, str]]:
    """Build the Browser Use adapter seeded for every flow. Returns the adapter and
    a flow_name → goal_id map."""
    seeds = {}
    goal_ids = {}
    for name in ("login", "search", "checkout"):
        seed = seed_for(name)
        seeds[seed.goal_id] = seed
        goal_ids[name] = seed.goal_id
    adapter = BrowserUseAdapter(
        store, target=Target(app=app, environment="staging"),
        seeds=seeds, current_version=APP_VERSION,
    )
    return adapter, goal_ids


def explore_and_write(adapter: BrowserUseAdapter, flow_name: str, goal_id: str) -> None:
    """Population phase: the agent explores the unmutated flow and writes back the
    success signals it actually observed (behavioral + network = two evidence types,
    giving the oracle diversity on top of the seed). One writer (Phase 0)."""
    outcome = simapp.run_memory(flow_name)
    observations = [
        ObservedSignal(
            kind="success", type=sig.type, value=sig.value, present=True,
            source_type="agent", source_id=EXPLORER, observed_app_version=APP_VERSION,
        )
        for sig in outcome.observed
    ]
    adapter.write_observations(goal_id, EXPLORER, observations, observed_app_version=APP_VERSION)


def oracle_said_success(kf: KnowledgeFile, observed: list[simapp.Sig]) -> bool:
    """The SHARED oracle's verdict for a run: success iff the run observed a signal
    that the projection currently BELIEVES (seeded or diversity-backed). A believed
    success signal only exists when the diversity-or-seed rule is satisfied, so this
    never fires on an unsupported single source."""
    believed = {s.value for s in kf.success_signals if s.status.value == "believed"}
    return bool(believed & {o.value for o in observed})
