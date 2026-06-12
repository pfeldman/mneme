"""Browser Use adapter - the Phase-0 runtime bridge.

Responsibilities (and ONLY these - ADR-0003):
  read_knowledge(goal)   -> project the believed knowledge and render it as guidance
                            an agent uses to REGENERATE its own steps (never replay).
  write_observations(.)  -> redact, then append immutable signal events to the store.
  write_candidates(.)    -> ADR-0014: redact + validate triggers, then append immutable
                            CandidateEvents (agent-proposed risks / uncertainties).

The actual `browser_use` package is an optional extra; it is imported LAZILY inside
`build_agent_task` so that importing this module (and the whole core) needs only
pydantic + pyyaml. Construct steps are deliberately absent: we hand the agent the
goal + believed success/failure oracles and let it find its own path.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..merge import project, project_with_seed
from ..model import KnowledgeFile, Risk, Target, Uncertainty
from ..model.trigger_validator import validate_risk
from ..store import (
    CandidateEvent,
    CandidateRiskPayload,
    CandidateUncertaintyPayload,
    DecayEvent,
    EventStore,
    ObservationEvent,
    ObservedSignal,
    RegressObservationEvent,
)
from .spi import redact, redact_observation


class CandidateRejected(ValueError):
    """A candidate risk failed the structured-trigger validator at the boundary.

    Raised by `write_candidates` when caller passes `strict_rejections=True`.
    The default path drops the rejected risk and records a note in the
    returned report; strict mode is for tests and gate-style checks where
    silently dropping would mask a regression.
    """


class BrowserUseAdapter:
    """Bridges an append-only store to a Browser Use agent for one app/target.

    A seed knowledge file per goal supplies the cold-start oracle (ADR-0005); agent
    observations are folded on top via the merge projection.

    With an `environment` selected (ADR-0035), every write is stamped with it and
    every read is filtered to that deployment's partition: evidence is per
    environment, knowledge (seeds) is product-level and folds into every
    environment's projection. An environment is NEVER a source dimension -
    `source_id` stays `agent_identity` and cross-environment observations never
    meet in one projection, so they can corroborate nothing (decision 5).
    """

    def __init__(
        self,
        store: EventStore,
        *,
        target: Target,
        seeds: dict[str, KnowledgeFile] | None = None,
        current_version: str | None = None,
        environment: str | None = None,
        legacy_env: str | None = None,
    ) -> None:
        self.store = store
        self.target = target
        self.seeds = seeds or {}
        self.current_version = current_version
        # ADR-0035: the deployment this adapter's run checks. Stamped on every
        # write; partitions every read. Empty string counts as unset (the
        # ADR-0034 posture), so None means "undeclared project": no filter,
        # byte-identical behavior to pre-ADR-0035.
        self.environment = environment or None
        # ADR-0035 decision 4: which declared environment pre-migration events
        # (environment None) are attributed to. A projection INPUT - no event
        # file is ever rewritten (the ADR-0013 caller-supplied-anchor posture).
        self.legacy_env = legacy_env or None

    # ---- SPI: read ----------------------------------------------------------

    def _environment_matches(self, event_environment: str | None) -> bool:
        """The ADR-0035 decision 4 partition rule, in one place.

        With NO environment selected (undeclared project) every event matches:
        today's behavior exactly. With one selected, an event matches when it
        carries the same name, PLUS (when config `legacy_env` names this
        environment) when it carries None - pre-migration history attributed
        to the named legacy deployment. Otherwise None-events match NO
        declared environment (honest exclusion: nobody recorded which
        deployment produced them)."""
        if self.environment is None:
            return True
        if event_environment == self.environment:
            return True
        return event_environment is None and self.legacy_env == self.environment

    def read_events(self, goal_id: str) -> list[ObservationEvent]:
        """The selected environment's promotable event stream for a goal.

        THE partition point (ADR-0035 decisions 4 + 5): the believed
        projection and the decay derivation both operate on this filtered
        stream, so evidence from one deployment can never corroborate, mask,
        or decay belief about another. `merge/` and `oracle/` are untouched -
        the core learns about environments only as a field on data it never
        interprets, and within one environment ADR-0005/0008/0012/0013/0029
        apply verbatim."""
        return [
            ev for ev in self.store.read(goal_id)
            if self._environment_matches(ev.environment)
        ]

    def read_decay_events(self, goal_id: str) -> list[DecayEvent]:
        """The selected environment's decay-flip log for a goal, filtered with
        the same partition rule as `read_events`, so replaying one
        environment's log reconstructs that environment's flips without
        touching another's (ADR-0035 decision 4)."""
        return [
            de for de in self.store.read_decay(goal_id)
            if self._environment_matches(de.environment)
        ]

    def read_knowledge(self, goal_id: str) -> KnowledgeFile | None:
        """Believed projection for a goal, or None if there is neither a seed nor
        any events for it. Computed PER ENVIRONMENT (ADR-0035 decision 4): the
        event stream is the selected environment's partition, while the seed
        folds into EVERY environment's projection (a human/spec seed is product
        intent, trusted from cold start in each deployment, ADR-0005)."""
        events = self.read_events(goal_id)
        seed = self.seeds.get(goal_id)
        if seed is not None:
            return project_with_seed(
                seed, events, current_version=self.current_version
            )
        if not events:
            return None
        # No seed: project from events alone (oracle stays unbelieving without
        # diversity - exactly the cold-start friction ADR-0005 intends).
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
            # ADR-0035 decision 4: operational provenance, like agent_id.
            # None on an undeclared project.
            environment=self.environment,
            signals=redacted,
        )
        self.store.append(event)

    def write_regress_observation(
        self,
        goal_id: str,
        agent_id: str,
        verdict: str,
        observations: list[ObservedSignal],
        observed_app_version: str | None = None,
        voids: list[str] | None = None,
    ) -> str:
        """Redact then append the regress run's audit record (ADR-0023 dec 4).

        Same redaction boundary as `write_observations` (docs/06 leakage: no
        secrets / tokens / PII reach the store), but the event lands in the
        sibling `regress/` subdir via `append_regress`, NOT in the promotable
        `events/` stream. The merge projection never reads it, so persisting a
        regress confirmation can never grow the believed set (ADR-0029); it only
        makes the verdict traceable after the fact."""
        redacted = [redact_observation(o) for o in observations]
        event = RegressObservationEvent(
            ts=datetime.now(timezone.utc),
            agent_id=agent_id,
            goal_id=goal_id,
            verdict=verdict,
            observed_app_version=observed_app_version or self.current_version,
            # ADR-0035 decision 4: which deployment this regress run checked.
            environment=self.environment,
            signals=redacted,
            # ADR-0033 decision 4: void confirmation reasons ride the same
            # record (redacted: they may quote agent-authored text).
            voids=[redact(v) for v in voids] if voids else None,
        )
        self.store.append_regress(event)
        return event.event_id

    def write_candidates(
        self,
        goal_id: str,
        agent_identity: str,
        new_risks: list[Risk] | None = None,
        new_uncertainties: list[Uncertainty] | None = None,
        observed_app_version: str | None = None,
        *,
        strict_rejections: bool = False,
    ) -> list[str]:
        """Append CandidateEvents for agent-proposed risks / uncertainties (ADR-0014).

        Behaviour:
          - Each Risk is validated through `validate_risk` (ADR-0009 sec 4 + ADR-
            0014 sec 3). A `rejected` outcome drops the risk; the caller can
            switch to strict mode (raise CandidateRejected) for tests.
          - `provenance.source_id` on a candidate Risk is FORCED to
            `agent_identity`; ditto `raised_by` on a candidate Uncertainty.
            The runner is the source of truth for the agent_identity anchor
            (ADR-0008): even if the executor returned a different source_id,
            the adapter rewrites it. A single agent cannot self-promote.
          - Risk / Uncertainty free-text fields (`description`, `mitigation`,
            `value`, `question`) are redacted at the boundary the same way
            ObservedSignal values are (docs/06 leakage rule).
          - Each accepted candidate becomes ONE CandidateEvent (so independent
            agents writing the same id produce two source ids in the
            projection). Returns the list of persisted event ids.

        The store-level `append_candidate` is append-only (ADR-0001): the
        original CandidateEvent is NEVER edited; human promotion appends a
        separate seed event (ADR-0014 sec 4).
        """
        version = observed_app_version or self.current_version
        out_ids: list[str] = []
        for risk in (new_risks or []):
            outcome = validate_risk(risk)
            if outcome.outcome == "rejected":
                if strict_rejections:
                    raise CandidateRejected(
                        f"candidate risk {risk.id!r} rejected: {outcome.reason}"
                    )
                # Silently drop (the runner surfaces these via its `notes` list
                # already; the adapter is the second line of defense).
                continue
            risk_redacted = _redact_risk(risk, agent_identity)
            event = CandidateEvent(
                ts=datetime.now(timezone.utc),
                agent_identity=agent_identity,
                goal_id=goal_id,
                observed_app_version=version,
                # ADR-0035 decisions 4 + 6: provenance for the review
                # annotation ("seen on dev2 only"); never corroboration.
                environment=self.environment,
                payload=CandidateRiskPayload(risk=risk_redacted),
            )
            self.store.append_candidate(event)
            out_ids.append(event.event_id)
        for unc in (new_uncertainties or []):
            unc_redacted = _redact_uncertainty(unc, agent_identity)
            event = CandidateEvent(
                ts=datetime.now(timezone.utc),
                agent_identity=agent_identity,
                goal_id=goal_id,
                observed_app_version=version,
                environment=self.environment,
                payload=CandidateUncertaintyPayload(uncertainty=unc_redacted),
            )
            self.store.append_candidate(event)
            out_ids.append(event.event_id)
        return out_ids

    # ---- Prompting: turn believed knowledge into step-regeneration guidance --

    @staticmethod
    def knowledge_to_prompt(kf: KnowledgeFile) -> str:
        """Render believed knowledge as agent guidance. It describes the GOAL and
        what success/failure look like - never a procedure. The agent regenerates
        its own steps; only believed/contested signals are surfaced, with their
        trust state, so the agent can weigh them."""
        lines = [
            f"Goal: {kf.goal}",
            f"Target app: {kf.target.app}"
            + (f" ({kf.target.environment})" if kf.target.environment else ""),
            "",
            "You decide the steps yourself. Use the knowledge below only to "
            "recognize progress and to confirm success - do not treat it as a script.",
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
            return f"Goal id: {goal_id}\n(no prior knowledge - explore from cold)"
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


def _redact_risk(risk: Risk, agent_identity: str) -> Risk:
    """Force `source_id = agent_identity` and redact free-text fields.

    Trigger.expect is structured, but the surrounding strings are free text
    that an agent could accidentally splash a token into; redact them with
    the same filter ObservedSignal values go through.
    """
    new_provenance = risk.provenance.model_copy(update={
        "source_id": agent_identity,
    })
    updates: dict[str, object] = {
        "description": redact(risk.description),
        "provenance": new_provenance,
    }
    if risk.mitigation is not None:
        updates["mitigation"] = redact(risk.mitigation)
    return risk.model_copy(update=updates)


def _redact_uncertainty(unc: Uncertainty, agent_identity: str) -> Uncertainty:
    """Force `raised_by = agent_identity` and redact the question text."""
    return unc.model_copy(update={
        "question": redact(unc.question),
        "raised_by": agent_identity,
    })
