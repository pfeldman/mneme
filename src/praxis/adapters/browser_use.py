"""Browser Use adapter — the Phase-0 runtime bridge.

Responsibilities (and ONLY these — ADR-0003):
  read_knowledge(goal)  -> project the believed knowledge and render it as guidance
                           an agent uses to REGENERATE its own steps (never replay).
  write_observations(.)  -> redact, then append immutable events to the store.

The actual `browser_use` package is an optional extra; it is imported LAZILY inside
`build_agent_task` so that importing this module (and the whole core) needs only
pydantic + pyyaml. Construct steps are deliberately absent: we hand the agent the
goal + believed success/failure oracles and let it find its own path.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..merge import project, project_with_seed
from ..model import KnowledgeFile, Target
from ..store import EventStore, ObservationEvent, ObservedSignal
from .spi import redact_observation


class BrowserUseAdapter:
    """Bridges an append-only store to a Browser Use agent for one app/target.

    A seed knowledge file per goal supplies the cold-start oracle (ADR-0005); agent
    observations are folded on top via the merge projection.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        target: Target,
        seeds: dict[str, KnowledgeFile] | None = None,
        current_version: str | None = None,
    ) -> None:
        self.store = store
        self.target = target
        self.seeds = seeds or {}
        self.current_version = current_version

    # ---- SPI: read ----------------------------------------------------------

    def read_knowledge(self, goal_id: str) -> KnowledgeFile | None:
        """Believed projection for a goal, or None if there is neither a seed nor
        any events for it."""
        events = self.store.read(goal_id)
        seed = self.seeds.get(goal_id)
        if seed is not None:
            return project_with_seed(
                seed, events, current_version=self.current_version
            )
        if not events:
            return None
        # No seed: project from events alone (oracle stays unbelieving without
        # diversity — exactly the cold-start friction ADR-0005 intends).
        return project(
            events,
            goal_id=goal_id,
            goal=goal_id,
            target=self.target,
            current_version=self.current_version,
        )

    # ---- SPI: write ---------------------------------------------------------

    def write_observations(
        self,
        goal_id: str,
        agent_id: str,
        observations: list[ObservedSignal],
        observed_app_version: str | None = None,
    ) -> None:
        """Redact then append one event with the agent's observations (ADR-0001)."""
        redacted = [redact_observation(o) for o in observations]
        event = ObservationEvent(
            ts=datetime.now(timezone.utc),
            agent_id=agent_id,
            goal_id=goal_id,
            observed_app_version=observed_app_version or self.current_version,
            signals=redacted,
        )
        self.store.append(event)

    # ---- Prompting: turn believed knowledge into step-regeneration guidance --

    @staticmethod
    def knowledge_to_prompt(kf: KnowledgeFile) -> str:
        """Render believed knowledge as agent guidance. It describes the GOAL and
        what success/failure look like — never a procedure. The agent regenerates
        its own steps; only believed/contested signals are surfaced, with their
        trust state, so the agent can weigh them."""
        lines = [
            f"Goal: {kf.goal}",
            f"Target app: {kf.target.app}"
            + (f" ({kf.target.environment})" if kf.target.environment else ""),
            "",
            "You decide the steps yourself. Use the knowledge below only to "
            "recognize progress and to confirm success — do not treat it as a script.",
            "",
            "Success looks like (trust state in brackets):",
        ]
        for s in kf.success_signals:
            lines.append(f"  - [{s.status.value}] ({s.type.value}) {s.value}")
        if kf.failure_signals:
            lines.append("")
            lines.append("Failure / dead-ends look like:")
            for s in kf.failure_signals:
                lines.append(f"  - [{s.status.value}] ({s.type.value}) {s.value}")
        return "\n".join(lines)

    def build_agent_task(self, goal_id: str) -> str:
        """Convenience: read believed knowledge and render the agent task string."""
        kf = self.read_knowledge(goal_id)
        if kf is None:
            return f"Goal id: {goal_id}\n(no prior knowledge — explore from cold)"
        return self.knowledge_to_prompt(kf)

    def build_agent(self, goal_id: str, **agent_kwargs: object) -> object:
        """Construct a live Browser Use `Agent` for the goal. Requires the
        `browser-use` extra; imported lazily so the core stays runtime-free."""
        try:
            from browser_use import Agent
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "the Browser Use adapter requires the optional extra: "
                "pip install 'praxis[browser-use]'"
            ) from exc
        return Agent(task=self.build_agent_task(goal_id), **agent_kwargs)
