"""Recency decay as projection-time derivation (ADR-0013).

Decay is a property of the PROJECTION over the immutable event log; the store
is never mutated. Two distinct outputs:

  - Confidence drift is pure (no event written). The numeric `confidence` field
    on a projected signal is derived at projection time from the underlying
    observation ages.
  - Status flips emit explicit `DecayEvent`s. When recency decay causes the
    `independent_diverse(...)` gate (ADR-0008) to fail on the surviving
    non-staled set, a projected signal transitions to `stale` and the
    projection driver appends a `DecayEvent` referencing the retired event
    ids, the anchor used, and the rule that fired.

This module is runtime-free (ADR-0003) and unit-testable without a store or a
browser. It exposes:

  - `DecayConfig`: pre-registered thresholds (N minor versions, T wall-clock
    days). Pinned by `praxis_git_sha` in the run manifest (ADR-0009 convention).
  - `select_current_version(...)`: deterministic anchor selection for the
    multi-writer / decay collision (ADR-0013 section 5).
  - `is_observation_staled(...)`: per-observation decay predicate.
  - `evaluate_decay(...)`: the single entry-point the projection calls. Takes
    the event log + the seed + the prior decay events and returns
      (surviving_observations, new_decay_events_to_append).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..model import SignalType
from ..oracle import (
    SignalSummary,
    agreeing_types,
    independent_diverse,
)
from ..store import DecayEvent, ObservationEvent, ObservedSignal


@dataclass(frozen=True)
class DecayConfig:
    """Pre-registered decay thresholds.

    `N` and `T` are pinned per experiment in the run manifest (same convention
    as the sigma-bounded kill gates in `docs/phase-1-experiment.md`). A writer
    cannot pass custom thresholds at projection time to coax a flip; the
    runner reads these from the experiment config and never from a writer.
    """

    # Staled when `observed_app_version` is more than `minor_versions_back`
    # minor versions behind `current_version`. Default 2 per ADR-0013.
    minor_versions_back: int = 2
    # Staled when the observation wall-clock is more than `stale_after_days`
    # behind `now`. Default 90 per ADR-0013.
    stale_after_days: float = 90.0
    # Major-version changes stale every prior signal for the goal regardless
    # of the minor-version delta. Default True per ADR-0013 section 3.
    major_bump_stales_all: bool = True


_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?"
)


def _parse_semver(version: str | None) -> tuple[int, int, int] | None:
    """Parse a semver-shaped tag into (major, minor, patch). Returns None for
    non-semver-shaped tags so the caller can fall through to wall-clock-only
    decay (ADR-0013 section 3)."""
    if not version:
        return None
    m = _SEMVER_RE.match(version.strip())
    if not m:
        return None
    major = int(m.group("major"))
    minor = int(m.group("minor") or 0)
    patch = int(m.group("patch") or 0)
    return (major, minor, patch)


def select_current_version(
    *,
    caller_supplied: str | None,
    observations: list[ObservedSignal],
) -> str | None:
    """Pick the projection's `current_version` per ADR-0013 section 5.

    Priority:
      1. The caller (the runner via the adapter) passes an explicit version.
         The projection does NOT pick a version from the log when the caller
         supplies one. CLI `praxis status` passes it explicitly; `praxis
         regress` reads it from the active `KnowledgeAdapter`'s app context.
      2. If the caller passes nothing, pick the highest-semver version present
         in the supporting set. Highest-semver is deterministic and monotonic
         across concurrent writers (independent of write order), so two
         parallel writers cannot flip the projection back and forth.
      3. If no event in the supporting set has a semver-shaped version, return
         None and let the caller fall through to wall-clock-only decay using
         the projection's run-start timestamp.
    """
    if caller_supplied is not None:
        return caller_supplied
    semver_versions: list[tuple[tuple[int, int, int], str]] = []
    for obs in observations:
        parsed = _parse_semver(obs.observed_app_version)
        if parsed is not None and obs.observed_app_version is not None:
            semver_versions.append((parsed, obs.observed_app_version))
    if not semver_versions:
        return None
    # max() is well-defined since the tuple keys total-order semver.
    return max(semver_versions, key=lambda t: t[0])[1]


def is_observation_staled(
    *,
    obs_version: str | None,
    obs_ts: datetime,
    current_version: str | None,
    now: datetime,
    config: DecayConfig,
) -> tuple[bool, str | None]:
    """Per-observation decay predicate. Returns `(staled, rule)` where `rule`
    is one of `"version"`, `"wallclock"`, `"both"`, or None when fresh.

    Version-anchor (primary): staled when the observed version is more than
    `N` minor versions behind the current version, or any major bump.
    Wall-clock (secondary): staled when the wall-clock age exceeds `T` days.
    Both anchors are evaluated; if both fire we report `"both"` so the audit
    trail captures the strongest reason.
    """
    by_version = False
    by_wallclock = False

    if current_version is not None:
        cur = _parse_semver(current_version)
        obs = _parse_semver(obs_version)
        if cur is not None and obs is not None:
            cur_major, cur_minor, _ = cur
            obs_major, obs_minor, _ = obs
            if config.major_bump_stales_all and obs_major < cur_major:
                by_version = True
            elif obs_major == cur_major and (cur_minor - obs_minor) > config.minor_versions_back:
                by_version = True
        # If the obs has no semver but the current does, leave version-decay
        # off for that obs and rely on wall-clock; non-semver tags fall
        # through per ADR-0013 section 3.

    age = now - obs_ts
    if age > timedelta(days=config.stale_after_days):
        by_wallclock = True

    if by_version and by_wallclock:
        return True, "both"
    if by_version:
        return True, "version"
    if by_wallclock:
        return True, "wallclock"
    return False, None


def _summary_for(
    obs_list: list[tuple[ObservedSignal, datetime]],
) -> SignalSummary:
    """Build a SignalSummary from a list of (obs, ts) pairs. The projection
    already does this; we replicate the relevant subset for decay
    re-evaluation so the decay module stays a self-contained module."""
    if not obs_list:
        raise ValueError("cannot build summary from empty observation list")
    first = obs_list[0][0]
    summary = SignalSummary(kind=first.kind, type=first.type, value=first.value)
    obs_list = sorted(obs_list, key=lambda p: p[1])
    for obs, ts in obs_list:
        summary.presence.append(obs.present)
        summary.source_types.add(obs.source_type)
        summary.source_ids.add(obs.source_id)
        if obs.observed_app_version:
            summary.observed_app_versions.add(obs.observed_app_version)
        if summary.last_verified is None or ts > summary.last_verified:
            summary.last_verified = ts
        if obs.confidence is not None:
            summary.seed_confidence = max(summary.seed_confidence or 0.0, obs.confidence)
    return summary


@dataclass(frozen=True)
class DecayEvaluation:
    """The output of `evaluate_decay()`.

    `surviving_observations` is the list of `(ObservedSignal, ts, event_id)`
    triples the projection should fold into summaries. Observations that were
    staled by recency decay are filtered out; same-type repeats from the same
    source cannot resurrect them (ADR-0013 section 2 and 4).

    `new_decay_events` is the list of `DecayEvent`s the projection driver
    should append. The projection itself does not write to the store; the
    runner/driver does, so this stays a pure derivation.

    `staled_signal_keys` records the grouping keys that decayed. The projection
    keeps these in its output as `stale` signals (with provenance pointing at
    the retired observations) so `praxis status` and `praxis review` see the
    flip; the events are not erased.
    """

    surviving_observations: list[tuple[ObservedSignal, datetime, str]]
    new_decay_events: list[DecayEvent]
    staled_signal_keys: set[tuple[str, SignalType, str]]


def evaluate_decay(
    *,
    events: list[ObservationEvent],
    goal_id: str,
    current_version: str | None,
    now: datetime,
    config: DecayConfig,
    prior_decay_events: list[DecayEvent] | None = None,
    seeded_observations: list[tuple[ObservedSignal, datetime]] | None = None,
) -> DecayEvaluation:
    """The single decay entry-point. Pure: same inputs, same outputs.

    Algorithm (ADR-0013):

      1. Flatten events into per-observation triples (obs, ts, event_id) for
         the goal.
      2. Partition each (signal_kind, type, value) group into staled and
         surviving observations using the version-primary + wall-clock
         secondary anchor.
      3. For each prior decay event that already retired a signal group:
         drop ALL observations whose event_id is in `retired_event_ids` AND
         that are non-fresh under the current anchor. Decay is unidirectional
         (ADR-0013 section 4): an already-retired observation cannot rejoin
         the surviving set just because a later same-type same-source obs
         lands fresh.
      4. Re-run `independent_diverse(...)` over the surviving SUCCESS set.
      5. Determine which signal groups, projected under the OLD set, would
         have been `believed` (the cold-start oracle gate held) but now fail
         the ADR-0008 gate against the surviving set. For each, emit a
         `DecayEvent` capturing the retired event ids and the rule that fired.
      6. Likewise for `contested` groups whose live evidence has fully aged
         out: flip to `stale` and emit a decay event (ADR-0013 section 1).

    `seeded_observations` are signals from the seed knowledge file (the
    cold-start oracle). They participate in the surviving set just like agent
    observations, since their `last_verified` is the projection's `now` per
    ADR-0005.
    """
    prior_decays = prior_decay_events or []
    seeded = seeded_observations or []

    # Step 1: flatten event observations.
    flat: list[tuple[ObservedSignal, datetime, str]] = []
    for ev in events:
        if ev.goal_id != goal_id:
            continue
        for obs in ev.signals:
            flat.append((obs, ev.ts, ev.event_id))

    # Index already-retired event ids per signal key so unidirectional decay
    # holds across projections: an observation that a previous projection
    # retired stays retired, even if the same agent re-asserts it later (the
    # later obs is a separate event_id and will be evaluated fresh on its own
    # merit; this only stops the OLD retired obs from coming back).
    retired_keys: dict[tuple[str, SignalType, str], set[str]] = {}
    for de in prior_decays:
        if de.goal_id != goal_id:
            continue
        key = (de.signal_kind, de.signal_type, de.signal_value)
        retired_keys.setdefault(key, set()).update(de.retired_event_ids)

    # Step 2: per-observation staleness check. Track which observations would
    # be excluded by decay so we can compute `independent_diverse` over the
    # surviving set.
    surviving: list[tuple[ObservedSignal, datetime, str]] = []
    retired_now: dict[tuple[str, SignalType, str], list[tuple[str, str]]] = {}
    by_key_all: dict[tuple[str, SignalType, str], list[tuple[ObservedSignal, datetime]]] = {}
    by_key_surviving: dict[
        tuple[str, SignalType, str], list[tuple[ObservedSignal, datetime]]
    ] = {}

    def _record(
        obs: ObservedSignal,
        ts: datetime,
        event_id: str,
        key: tuple[str, SignalType, str],
        staled_flag: bool,
        rule: str | None,
    ) -> None:
        by_key_all.setdefault(key, []).append((obs, ts))
        if staled_flag:
            retired_now.setdefault(key, []).append((event_id, rule or "wallclock"))
            return
        surviving.append((obs, ts, event_id))
        by_key_surviving.setdefault(key, []).append((obs, ts))

    # Seeded observations: stamped at `now`, so they never decay by wall-clock,
    # but if the seed carries an `observed_app_version` more than N minors
    # behind current, version-decay still fires (ADR-0013: "A correct seed for
    # an oracle that no current app version still exposes decays to stale").
    for obs, ts in seeded:
        key = (obs.kind, obs.type, obs.value)
        staled, rule = is_observation_staled(
            obs_version=obs.observed_app_version,
            obs_ts=ts,
            current_version=current_version,
            now=now,
            config=config,
        )
        _record(obs, ts, f"seed:{obs.value}", key, staled, rule)

    for obs, ts, event_id in flat:
        key = (obs.kind, obs.type, obs.value)
        if event_id in retired_keys.get(key, set()):
            # Already retired by a prior projection. Unidirectional decay:
            # this observation cannot rejoin the surviving set.
            by_key_all.setdefault(key, []).append((obs, ts))
            continue
        staled, rule = is_observation_staled(
            obs_version=obs.observed_app_version,
            obs_ts=ts,
            current_version=current_version,
            now=now,
            config=config,
        )
        _record(obs, ts, event_id, key, staled, rule)

    # Step 4: success-set re-evaluation. The diversity gate is goal-level.
    success_keys_all = [k for k in by_key_all if k[0] == "success"]
    success_summaries_surviving: list[SignalSummary] = []
    for k in success_keys_all:
        obs_list = by_key_surviving.get(k, [])
        if obs_list:
            success_summaries_surviving.append(_summary_for(obs_list))

    surviving_independent = independent_diverse(success_summaries_surviving)
    surviving_agreeing = agreeing_types(success_summaries_surviving)

    # For "would have been believed" we need the OLD verdict: project once
    # over the full set (pre-decay) and see which success groups passed.
    success_summaries_all: list[SignalSummary] = []
    for k in success_keys_all:
        obs_list = by_key_all.get(k, [])
        if obs_list:
            success_summaries_all.append(_summary_for(obs_list))

    historical_independent = independent_diverse(success_summaries_all)

    # Step 5/6: status-flip detection.
    new_decay_events: list[DecayEvent] = []
    staled_keys: set[tuple[str, SignalType, str]] = set()

    for full_key in by_key_all:
        kind, sig_type, value = full_key
        retired_for_key = retired_now.get(full_key, [])
        if not retired_for_key:
            continue
        # The full historical summary (pre-decay) and the surviving one tell
        # us whether THIS signal's projected status flips.
        full_summary = _summary_for(by_key_all[full_key])
        surviving_obs_list = by_key_surviving.get(full_key, [])
        surviving_summary = _summary_for(surviving_obs_list) if surviving_obs_list else None

        was_believed = _signal_was_believed(
            full_summary,
            success_summaries=success_summaries_all,
            historical_independent=historical_independent,
        )
        is_believed_now = _signal_is_believed_now(
            surviving_summary,
            surviving_independent=surviving_independent,
            surviving_agreeing=surviving_agreeing,
        )
        was_contested_with_live_evidence = (
            _signal_was_contested(full_summary) and not is_believed_now
        )
        contested_aged_out = (
            was_contested_with_live_evidence
            and surviving_obs_list == []
            and len(by_key_all[full_key]) > 0
        )

        flip_to_stale = (was_believed and not is_believed_now) or contested_aged_out
        if not flip_to_stale:
            continue

        from_status = "believed" if was_believed else "contested"
        retired_ids = [eid for eid, _ in retired_for_key]
        rules = {r for _, r in retired_for_key}
        fired_rule: str
        if rules == {"version"}:
            fired_rule = "version"
        elif rules == {"wallclock"}:
            fired_rule = "wallclock"
        else:
            fired_rule = "both"

        staled_keys.add((kind, sig_type, value))
        new_decay_events.append(
            DecayEvent(
                goal_id=goal_id,
                signal_kind=kind,  # type: ignore[arg-type]
                signal_type=sig_type,
                signal_value=value,
                from_status=from_status,  # type: ignore[arg-type]
                to_status="stale",
                retired_event_ids=retired_ids,
                anchor_current_version=current_version,
                anchor_now=now if now.tzinfo else now.replace(tzinfo=timezone.utc),
                rule=fired_rule,  # type: ignore[arg-type]
                note=(
                    f"projection decay: N={config.minor_versions_back} minors, "
                    f"T={config.stale_after_days}d"
                ),
            )
        )

    return DecayEvaluation(
        surviving_observations=surviving,
        new_decay_events=new_decay_events,
        staled_signal_keys=staled_keys,
    )


def _signal_was_believed(
    summary: SignalSummary,
    *,
    success_summaries: list[SignalSummary],
    historical_independent: bool,
) -> bool:
    """Reproduce the cold-start verdict before decay for one signal.

    Mirrors the relevant slice of `classify()`: a seeded, mostly-present
    signal is `believed`; an agent signal is `believed` only when the
    goal-level `independent_diverse` gate holds AND this signal participates
    in the agreeing-types set (>=2 stable types).
    """
    from ..oracle.trust import (
        _stable,
        agreeing_types as _agreeing,
        has_contradiction,
        is_flip_flop,
    )

    if is_flip_flop(summary.presence) or has_contradiction(summary.presence):
        return False
    if summary.is_seeded and summary.mostly_present:
        return True
    if summary.kind != "success":
        return False
    agreeing = _agreeing(success_summaries)
    return (
        summary.mostly_present
        and historical_independent
        and summary.type in agreeing
        and len(agreeing) >= 2
        and _stable(summary)
    )


def _signal_is_believed_now(
    summary: SignalSummary | None,
    *,
    surviving_independent: bool,
    surviving_agreeing: set[SignalType],
) -> bool:
    """Mirror of `_signal_was_believed` for the surviving set."""
    from ..oracle.trust import _stable, has_contradiction, is_flip_flop

    if summary is None:
        return False
    if is_flip_flop(summary.presence) or has_contradiction(summary.presence):
        return False
    if summary.is_seeded and summary.mostly_present:
        return True
    if summary.kind != "success":
        return False
    return (
        summary.mostly_present
        and surviving_independent
        and summary.type in surviving_agreeing
        and len(surviving_agreeing) >= 2
        and _stable(summary)
    )


def _signal_was_contested(summary: SignalSummary) -> bool:
    """Reproduce the contested verdict: positive vs negative disagreement on a
    signal that is not flip-flopping. Decay flips `contested` to `stale` only
    when the entire live supporting set ages out (ADR-0013 section 1)."""
    from ..oracle.trust import has_contradiction, is_flip_flop

    if is_flip_flop(summary.presence):
        return False
    return has_contradiction(summary.presence)
