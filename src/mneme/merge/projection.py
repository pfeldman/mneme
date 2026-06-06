"""`project(events) -> KnowledgeFile`: the believed projection.

How a believed state is built (never last-write-wins):
  1. Collect every raw `ObservedSignal` from every event for the goal, plus any
     seeded signals from a seed knowledge file.
  2. Group by (kind, type, value) into one `SignalSummary` each, ordered by time.
  3. Ask the oracle the goal-level diversity-or-seed question once.
  4. Classify each summary's Status and compute its confidence.
  5. Emit a `KnowledgeFile` whose signals carry aggregated provenance.

Contradictions are kept as separate `contested` signals; oscillation is
`quarantined`; nothing is dropped. The store stays the source of truth — re-running
`project` on the same events always yields the same believed state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..model import (
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    SourceType,
    Target,
)
from ..oracle import (
    SignalSummary,
    TrustConfig,
    agreeing_types,
    classify,
    confidence_of,
    oracle_believed,
)
from ..store import ObservationEvent, ObservedSignal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Obs:
    """A flattened observation with its timestamp, used to build summaries."""

    __slots__ = ("kind", "type", "value", "present", "ts", "source_type",
                 "source_id", "app_version", "confidence")

    def __init__(self, sig: ObservedSignal, ts: datetime) -> None:
        self.kind = sig.kind
        self.type = sig.type
        self.value = sig.value
        self.present = sig.present
        self.ts = ts
        self.source_type = sig.source_type
        self.source_id = sig.source_id
        self.app_version = sig.observed_app_version
        self.confidence = sig.confidence


def _summaries(observations: list[_Obs]) -> dict[tuple[str, object, str], SignalSummary]:
    """Group flattened observations into SignalSummary objects, time-ordered."""
    observations = sorted(observations, key=lambda o: o.ts)
    grouped: dict[tuple[str, object, str], SignalSummary] = {}
    for o in observations:
        key = (o.kind, o.type, o.value)
        s = grouped.get(key)
        if s is None:
            s = SignalSummary(kind=o.kind, type=o.type, value=o.value)
            grouped[key] = s
        s.presence.append(o.present)
        s.source_types.add(o.source_type)
        s.source_ids.add(o.source_id)
        if o.app_version:
            s.observed_app_versions.add(o.app_version)
        if s.last_verified is None or o.ts > s.last_verified:
            s.last_verified = o.ts
        if o.confidence is not None:
            s.seed_confidence = max(s.seed_confidence or 0.0, o.confidence)
    return grouped


def _provenance_for(summary: SignalSummary) -> Provenance:
    """Aggregate provenance for a believed signal. Prefer a seed source (it is the
    trust anchor); otherwise use any contributing source. `observation_count` is
    the number of times this exact signal was seen (within-signal evidence)."""
    if summary.is_seeded:
        src_type = SourceType.SPEC if SourceType.SPEC in summary.source_types else SourceType.HUMAN
    else:
        src_type = SourceType.AGENT
    # Deterministic representative id: sorted join keeps projection reproducible.
    source_id = ",".join(sorted(summary.source_ids)) if summary.source_ids else "unknown"
    app_version = sorted(summary.observed_app_versions)[-1] if summary.observed_app_versions \
        else None
    return Provenance(
        source_type=src_type,
        source_id=source_id,
        observed_app_version=app_version,
        last_verified=summary.last_verified or _utcnow(),
        observation_count=max(1, summary.observation_count),
    )


def project(
    events: list[ObservationEvent],
    *,
    goal_id: str,
    goal: str,
    target: Target,
    seeded: list[ObservedSignal] | None = None,
    now: datetime | None = None,
    current_version: str | None = None,
    config: TrustConfig | None = None,
) -> KnowledgeFile:
    """Fold events into the believed `KnowledgeFile` for one goal.

    `seeded` are signals authored by a human/spec (the cold-start oracle); they are
    folded in alongside agent observations. `current_version` flags signals not seen
    under the version being tested as `stale`.
    """
    cfg = config or TrustConfig()
    at = now or _utcnow()

    obs: list[_Obs] = []
    # Seeded signals are treated as observations with an explicit timestamp so they
    # participate in the same grouping/decay machinery.
    for s in seeded or []:
        ts = at  # seeds are considered verified "as of" the projection time
        obs.append(_Obs(s, ts))
    for ev in events:
        if ev.goal_id != goal_id:
            continue
        for s in ev.signals:
            obs.append(_Obs(s, ev.ts))

    summaries = _summaries(obs)
    success = [s for s in summaries.values() if s.kind == "success"]
    failure = [s for s in summaries.values() if s.kind == "failure"]

    diverse = oracle_believed(success)
    agreeing = agreeing_types(success)

    def build(summaries_subset: list[SignalSummary]) -> list[Signal]:
        out: list[Signal] = []
        for s in summaries_subset:
            status = classify(
                s,
                oracle_diverse=diverse,
                agreeing=agreeing,
                now=at,
                current_version=current_version,
                config=cfg,
            )
            out.append(
                Signal(
                    type=s.type,
                    value=s.value,
                    provenance=_provenance_for(s),
                    confidence=confidence_of(s, now=at, config=cfg),
                    status=status,
                )
            )
        # Stable, durability-ordered output (behavioral first), then by value.
        type_order = list(type(out[0].type).__members__.values()) if out else []
        out.sort(key=lambda sig: (type_order.index(sig.type) if sig.type in type_order else 99,
                                  sig.value))
        return out

    success_signals = build(success)
    if not success_signals:
        raise ValueError(
            f"cannot project goal {goal_id!r}: no success signals (seed the oracle first, "
            "ADR-0005)"
        )

    all_ts = [o.ts for o in obs]
    agents = sorted({o.source_id for o in obs if o.source_type == SourceType.AGENT})
    meta = Meta(
        created_at=min(all_ts) if all_ts else at,
        updated_at=max(all_ts) if all_ts else at,
        contributing_agents=agents or None,
    )

    return KnowledgeFile(
        schema_version="0",
        goal_id=goal_id,
        goal=goal,
        target=target,
        success_signals=success_signals,
        failure_signals=build(failure) or None,
        meta=meta,
    )


def project_with_seed(
    seed: KnowledgeFile,
    events: list[ObservationEvent],
    *,
    now: datetime | None = None,
    current_version: str | None = None,
    config: TrustConfig | None = None,
) -> KnowledgeFile:
    """Fold agent events onto a seeded knowledge file (the cold-start case).

    The seed defines the goal frame (goal text, target) and the trusted oracle; its
    signals are converted to seeded observations and merged with agent events.
    """
    seeded_obs: list[ObservedSignal] = []
    for kind, signals in (("success", seed.success_signals),
                          ("failure", seed.failure_signals or [])):
        for sig in signals:
            seeded_obs.append(
                ObservedSignal(
                    kind=kind,  # type: ignore[arg-type]
                    type=sig.type,
                    value=sig.value,
                    present=True,
                    source_type=sig.provenance.source_type,
                    source_id=sig.provenance.source_id,
                    observed_app_version=sig.provenance.observed_app_version,
                    confidence=sig.confidence,
                )
            )
    return project(
        events,
        goal_id=seed.goal_id,
        goal=seed.goal,
        target=seed.target,
        seeded=seeded_obs,
        now=now,
        current_version=current_version,
        config=config,
    )
