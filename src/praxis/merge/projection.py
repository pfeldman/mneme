"""`project(events) -> KnowledgeFile`: the believed projection.

How a believed state is built (never last-write-wins):
  1. Collect every raw `ObservedSignal` from every event for the goal, plus any
     seeded signals from a seed knowledge file.
  2. Run the ADR-0013 decay evaluation: drop observations staled by version or
     wall-clock anchors; carry forward unidirectional decay from prior
     `DecayEvent`s in the log; emit new `DecayEvent`s for status flips.
  3. Group by (kind, type, value) into one `SignalSummary` each, ordered by time.
  4. Ask the oracle the goal-level diversity-or-seed question once over the
     SURVIVING set.
  5. Classify each summary's Status and compute its confidence; signals that
     decayed mid-projection are forced to `stale`.
  6. Emit a `KnowledgeFile` whose signals carry aggregated provenance.

Contradictions are kept as separate `contested` signals; oscillation is
`quarantined`; nothing is dropped. The store stays the source of truth - re-running
`project` on the same events always yields the same believed state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..model import (
    KnowledgeFile,
    Meta,
    Provenance,
    Signal,
    SignalType,
    SourceType,
    Status,
    Target,
)
from ..oracle import (
    SignalSummary,
    TrustConfig,
    agreeing_types,
    classify,
    confidence_of,
    independent_diverse,
    is_stale,
)
from ..store import DecayEvent, ObservationEvent, ObservedSignal
from .decay import DecayConfig, evaluate_decay, select_current_version


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

    # Independence/diversity are judged over FRESH success evidence only: a stale
    # signal (off-version or aged) must not lend its type/source to corroborate new
    # claims, or an old seed could prop up brand-new assertions across releases.
    fresh_success = [
        s for s in success
        if not is_stale(s, now=at, current_version=current_version, config=cfg)
    ]
    independent = independent_diverse(fresh_success)
    agreeing = agreeing_types(fresh_success)

    def build(summaries_subset: list[SignalSummary],
              peers: list[SignalSummary] | None) -> list[Signal]:
        out: list[Signal] = []
        for s in summaries_subset:
            status = classify(
                s,
                oracle_independent=independent,
                agreeing=agreeing,
                now=at,
                current_version=current_version,
                config=cfg,
                peers=peers,
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

    # Per-signal corroboration (ADR-0029) is judged over the FRESH success set,
    # the same set the goal-level flag came from: a stale peer must lend no
    # corroboration to a fresh claim. Failure signals keep the legacy goal-level
    # fallback (peers=None); the per-signal success rule does not touch them.
    success_signals = build(success, fresh_success)
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
        failure_signals=build(failure, None) or None,
        meta=meta,
    )


def _passthrough_seed_fields(seed: KnowledgeFile, kf: KnowledgeFile) -> KnowledgeFile:
    """Phase-1 passthrough: seeded risks + uncertainties + auth_state survive projection.

    Phase 1 does not aggregate risks across events (that is Phase 2 when
    multi-writer corroboration matters); the seed is the source of truth for
    risks and uncertainties, and E-mode emits NEW candidates as store events
    that `praxis review` promotes via the human-in-the-loop seam (docs/05).
    Until then, downstream consumers (E-mode prompt rendering) need the
    seeded risks/uncertainties to appear in the believed projection.

    `auth_state` is carried through the same way (ADR-0026 decision 5 read path):
    the seed records the ABSTRACT ADR-0017 `auth_state` (`authenticated` plus
    `scope`); the projection must preserve it so the aggregate regress read path
    yields a `KnowledgeFile` whose `auth_state` the classifier's
    `_expected_authenticated_scope` can read ORGANICALLY. Without this, a goal
    that teach seeded as authenticated would project to no auth_state and an
    expired session could never classify as AUTH-EXPIRED through the real read
    path. The session itself (cookies / tokens) is never knowledge and never
    touched here; only the abstract posture rides through. A seed with no
    `auth_state` still projects to no `auth_state` (additive and safe).
    """
    return kf.model_copy(update={
        "risks": list(seed.risks) if seed.risks else None,
        "uncertainties": list(seed.uncertainties) if seed.uncertainties else None,
        "auth_state": seed.auth_state,
    })


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
    projected = project(
        events,
        goal_id=seed.goal_id,
        goal=seed.goal,
        target=seed.target,
        seeded=seeded_obs,
        now=now,
        current_version=current_version,
        config=config,
    )
    return _passthrough_seed_fields(seed, projected)


def project_with_decay(
    events: list[ObservationEvent],
    *,
    goal_id: str,
    goal: str,
    target: Target,
    seeded: list[ObservedSignal] | None = None,
    now: datetime | None = None,
    current_version: str | None = None,
    config: TrustConfig | None = None,
    decay_config: DecayConfig | None = None,
    prior_decay_events: list[DecayEvent] | None = None,
) -> tuple[KnowledgeFile, list[DecayEvent]]:
    """Phase 2 projection with explicit recency decay (ADR-0013).

    Returns:
      - `KnowledgeFile`: the believed projection over the SURVIVING set.
      - `list[DecayEvent]`: new decay events the caller (runner) must append
        to the store. Decay events are written ONLY for status flips
        (`believed` -> `stale`, `contested` with no live evidence -> `stale`).
        Pure confidence drift does NOT emit an event.

    The caller is responsible for:
      - Selecting `current_version` (typically passed by the runner; the
        projection falls back to `select_current_version()` over the supporting
        set when `None`).
      - Appending the returned decay events to the store in the same operation
        as reading the projection; otherwise the next projection re-derives
        them. Replaying the log after the append yields the same status
        without re-reading the clock (ADR-0013 section 1).

    The diversity check (`independent_diverse`) is re-run over the surviving
    set, so:
      - Same-type repeats from the same `source_id` cannot keep `believed`
        alive after the diverse signal stales (ADR-0013 section 2).
      - Decay is unidirectional: a signal staled by a prior decay event does
        not un-stale on a same-type repeat; re-promotion goes through the
        ADR-0008 cold-start gate (ADR-0013 section 4).
    """
    cfg = config or TrustConfig()
    dcfg = decay_config or DecayConfig()
    at = now or _utcnow()
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)

    # Resolve the anchor per ADR-0013 section 5. Caller-supplied wins; else
    # highest-semver across the supporting set; else None (wall-clock only).
    all_observations: list[ObservedSignal] = list(seeded or [])
    for ev in events:
        if ev.goal_id != goal_id:
            continue
        all_observations.extend(ev.signals)
    anchor_version = select_current_version(
        caller_supplied=current_version,
        observations=all_observations,
    )

    # Seeded observations are stamped at `now`, matching the existing
    # `project()` semantics.
    seeded_pairs: list[tuple[ObservedSignal, datetime]] = []
    for s in seeded or []:
        seeded_pairs.append((s, at))

    evaluation = evaluate_decay(
        events=events,
        goal_id=goal_id,
        current_version=anchor_version,
        now=at,
        config=dcfg,
        prior_decay_events=prior_decay_events,
        seeded_observations=seeded_pairs,
    )

    # Rebuild an "events view" that drops retired observations. This lets us
    # reuse `project()` unchanged (and keep its full classification path) over
    # the surviving set. We synthesize one event per surviving observation;
    # the source_id and ts are preserved so the projection's grouping is
    # equivalent. Seeded surviving observations are carried via the `seeded`
    # argument.
    surviving_events: list[ObservationEvent] = []
    surviving_seeded: list[ObservedSignal] = []
    seed_value_set = {(s.kind, s.type, s.value) for s in seeded or []}
    for obs, ts, event_id in evaluation.surviving_observations:
        key = (obs.kind, obs.type, obs.value)
        if event_id.startswith("seed:") and key in seed_value_set:
            surviving_seeded.append(obs)
            continue
        surviving_events.append(
            ObservationEvent(
                event_id=event_id,
                ts=ts,
                agent_id=obs.source_id,
                goal_id=goal_id,
                observed_app_version=obs.observed_app_version,
                signals=[obs],
            )
        )

    try:
        projected = project(
            surviving_events,
            goal_id=goal_id,
            goal=goal,
            target=target,
            seeded=surviving_seeded,
            now=at,
            current_version=anchor_version,
            config=cfg,
        )
    except ValueError:
        # All success observations were retired by decay. Build a stale-only
        # projection from the retired representatives so `praxis status` and
        # `praxis review` still see the goal frame and the flip is visible.
        projected = _build_stale_only_projection(
            events=events,
            goal_id=goal_id,
            goal=goal,
            target=target,
            staled_keys=evaluation.staled_signal_keys,
            now=at,
            anchor_version=anchor_version,
        )

    # Force `stale` status on any signal the decay evaluation flipped, even if
    # the projection-over-surviving-set classification would have computed
    # something else. The retired observations still need to surface in the
    # output (so `praxis review` sees them) - but their status is the flip.
    if evaluation.staled_signal_keys:
        projected = _apply_decay_flips(
            projected,
            events=events,
            goal_id=goal_id,
            staled_keys=evaluation.staled_signal_keys,
            now=at,
            anchor_version=anchor_version,
        )

    return projected, evaluation.new_decay_events


def _build_stale_only_projection(
    *,
    events: list[ObservationEvent],
    goal_id: str,
    goal: str,
    target: Target,
    staled_keys: set[tuple[str, SignalType, str]],
    now: datetime,
    anchor_version: str | None,
) -> KnowledgeFile:
    """Construct a KnowledgeFile from retired observations alone, with every
    signal forced to `stale`. Used when recency decay retires every success
    observation: the projection still has to expose the goal frame + the
    stale rows so the audit trail is visible in `praxis status`."""
    representative: dict[
        tuple[str, SignalType, str], tuple[ObservedSignal, datetime]
    ] = {}
    for ev in events:
        if ev.goal_id != goal_id:
            continue
        for obs in ev.signals:
            key = (obs.kind, obs.type, obs.value)
            if key in staled_keys:
                cur = representative.get(key)
                if cur is None or ev.ts > cur[1]:
                    representative[key] = (obs, ev.ts)

    success: list[Signal] = []
    failure: list[Signal] = []
    all_ts: list[datetime] = []
    for (kind, sig_type, value), (obs, ts) in representative.items():
        all_ts.append(ts)
        stale_signal = Signal(
            type=sig_type,
            value=value,
            provenance=Provenance(
                source_type=obs.source_type,
                source_id=obs.source_id,
                observed_app_version=obs.observed_app_version or anchor_version,
                last_verified=ts,
                observation_count=1,
            ),
            confidence=0.0,
            status=Status.STALE,
        )
        if kind == "success":
            success.append(stale_signal)
        else:
            failure.append(stale_signal)

    if not success:
        # No success representatives either - synthesize a single stale row
        # so the schema's min_length=1 success_signals constraint holds. The
        # row is loud (status=stale) so the operator immediately sees the
        # projection has no live oracle.
        success.append(
            Signal(
                type=SignalType.BEHAVIORAL,
                value="oracle fully decayed - no surviving success evidence",
                provenance=Provenance(
                    source_type=SourceType.AGENT,
                    source_id="projection-driver",
                    observed_app_version=anchor_version,
                    last_verified=now,
                    observation_count=1,
                ),
                confidence=0.0,
                status=Status.STALE,
            )
        )

    meta = Meta(
        created_at=min(all_ts) if all_ts else now,
        updated_at=max(all_ts) if all_ts else now,
    )
    return KnowledgeFile(
        schema_version="0",
        goal_id=goal_id,
        goal=goal,
        target=target,
        success_signals=success,
        failure_signals=failure or None,
        meta=meta,
    )


def _apply_decay_flips(
    kf: KnowledgeFile,
    *,
    events: list[ObservationEvent],
    goal_id: str,
    staled_keys: set[tuple[str, SignalType, str]],
    now: datetime,
    anchor_version: str | None,
) -> KnowledgeFile:
    """Inject `stale` rows into `kf` for each signal key the decay evaluation
    flipped that did not survive into the projected output, and force
    `Status.STALE` on any rows that did survive but were retired."""

    def _existing_keys(signals: list[Signal]) -> set[tuple[SignalType, str]]:
        return {(s.type, s.value) for s in signals}

    # Force STALE on present rows.
    new_success = [
        s.model_copy(update={"status": Status.STALE})
        if ("success", s.type, s.value) in staled_keys else s
        for s in kf.success_signals
    ]
    new_failure = (
        [
            s.model_copy(update={"status": Status.STALE})
            if ("failure", s.type, s.value) in staled_keys else s
            for s in kf.failure_signals
        ]
        if kf.failure_signals else None
    )

    # Inject phantom STALE rows for signal groups that were fully retired
    # (no surviving observation remained so the projection dropped them).
    existing_success_keys = _existing_keys(new_success)
    existing_failure_keys = _existing_keys(new_failure or [])

    # Collect a representative observation for each retired key so we can
    # build a Signal with provenance.
    representative: dict[
        tuple[str, SignalType, str], tuple[ObservedSignal, datetime]
    ] = {}
    for ev in events:
        if ev.goal_id != goal_id:
            continue
        for obs in ev.signals:
            key = (obs.kind, obs.type, obs.value)
            if key in staled_keys:
                cur = representative.get(key)
                if cur is None or ev.ts > cur[1]:
                    representative[key] = (obs, ev.ts)

    extra_success: list[Signal] = []
    extra_failure: list[Signal] = []
    for (kind, sig_type, value), (obs, ts) in representative.items():
        type_value = (sig_type, value)
        if kind == "success" and type_value in existing_success_keys:
            continue
        if kind == "failure" and type_value in existing_failure_keys:
            continue
        stale_signal = Signal(
            type=sig_type,
            value=value,
            provenance=Provenance(
                source_type=obs.source_type,
                source_id=obs.source_id,
                observed_app_version=obs.observed_app_version or anchor_version,
                last_verified=ts,
                observation_count=1,
            ),
            confidence=0.0,
            status=Status.STALE,
        )
        if kind == "success":
            extra_success.append(stale_signal)
        else:
            extra_failure.append(stale_signal)

    success_signals = new_success + extra_success
    failure_signals: list[Signal] | None
    if new_failure or extra_failure:
        failure_signals = (new_failure or []) + extra_failure
    else:
        failure_signals = None

    # Re-sort to keep determinism (durability order, then value).
    type_order = list(type(success_signals[0].type).__members__.values()) if success_signals else []
    success_signals.sort(
        key=lambda sig: (type_order.index(sig.type) if sig.type in type_order else 99, sig.value)
    )
    if failure_signals:
        failure_signals.sort(
            key=lambda sig: (type_order.index(sig.type) if sig.type in type_order else 99, sig.value)
        )

    return kf.model_copy(update={
        "success_signals": success_signals,
        "failure_signals": failure_signals,
    })
