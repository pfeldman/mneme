"""Trust scoring + status classification for signals (ADR-0005).

`merge` aggregates raw events into one `SignalSummary` per (kind, type, value) and
calls these pure functions to decide confidence and status. Keeping them pure and
runtime-free is deliberate: the trust logic is the product, and it must be unit
testable without a store or a browser.

Status semantics in the Phase-0 four-value enum (see ADR-0006):
  - quarantined : presence oscillates across runs (flip-flop) — untrustworthy.
  - contested   : positive AND negative observations disagree, OR a lone
                  agent-observed type that is consistent but NOT yet corroborated
                  by a different-type signal or a seed. "Contested" is the
                  not-yet-trustworthy bucket; it is never promoted to an oracle.
  - stale       : aged past the staleness horizon, or only seen under app versions
                  other than the current one. Demoted, never deleted.
  - believed    : seeded (human/spec), OR part of a diverse set of >=2 agreeing
                  success types. Only `believed` success signals form an oracle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..model import SignalType, SourceType, Status

SEED_SOURCES = frozenset({SourceType.HUMAN, SourceType.SPEC})


@dataclass(frozen=True)
class TrustConfig:
    """Knobs for confidence decay and staleness. Defaults are conservative."""

    half_life_days: float = 30.0  # confidence halves every N days since last_verified
    stale_after_days: float = 90.0  # older than this with no recent confirmation → stale
    seed_confidence_floor: float = 0.9  # seeded signals are trusted from cold-start


@dataclass
class SignalSummary:
    """Time-ordered aggregate of every observation of one (kind, type, value)."""

    kind: str  # "success" | "failure"
    type: SignalType
    value: str
    # presence per observation, ordered oldest→newest (True=seen, False=explicitly not seen)
    presence: list[bool] = field(default_factory=list)
    last_verified: datetime | None = None
    source_types: set[SourceType] = field(default_factory=set)
    source_ids: set[str] = field(default_factory=set)
    observed_app_versions: set[str] = field(default_factory=set)
    seed_confidence: float | None = None  # explicit floor carried by a seeded signal

    @property
    def observation_count(self) -> int:
        return len(self.presence)

    @property
    def is_seeded(self) -> bool:
        return bool(self.source_types & SEED_SOURCES)

    @property
    def positives(self) -> int:
        return sum(self.presence)

    @property
    def negatives(self) -> int:
        return sum(1 for p in self.presence if not p)

    @property
    def mostly_present(self) -> bool:
        """A consistently-present signal: at least one observation and no negatives."""
        return self.observation_count > 0 and self.negatives == 0


def is_flip_flop(presence: list[bool]) -> bool:
    """True if presence oscillates: >=2 transitions across the time-ordered runs
    (e.g. seen → not seen → seen). One transition is a contradiction, not yet a
    flip-flop; two means the signal is genuinely unstable → quarantine."""
    transitions = sum(1 for a, b in zip(presence, presence[1:]) if a != b)
    return transitions >= 2


def has_contradiction(presence: list[bool]) -> bool:
    """True if the signal was both observed and explicitly not-observed (any mix of
    True/False) — disagreement that must be preserved as `contested`, never
    silently resolved by last-write-wins."""
    return any(presence) and not all(presence)


def _stable(s: SignalSummary) -> bool:
    """Consistently present and not oscillating — eligible to count as evidence."""
    return s.mostly_present and not is_flip_flop(s.presence)


def agreeing_types(success: list[SignalSummary]) -> set[SignalType]:
    """Distinct success `type`s that are consistently present and stable (not
    flip-flopping). These are the candidate evidence the diversity rule counts."""
    return {s.type for s in success if _stable(s)}


def independent_diverse(success: list[SignalSummary]) -> bool:
    """Evidence-type diversity AND source-independence (ADR-0008). The stable
    success signals must span >=2 DIFFERENT types AND >=2 DISTINCT sources. This
    closes the poisoning vector where a SINGLE source fabricates two evidence types
    and self-corroborates: type-diversity alone is not independence. A seed counts as
    one independent source."""
    stable = [s for s in success if _stable(s)]
    types = {s.type for s in stable}
    if len(types) < 2:
        return False
    sources: set[str] = set()
    for s in stable:
        sources |= s.source_ids
    return len(sources) >= 2


def oracle_believed(success: list[SignalSummary]) -> bool:
    """The diversity-or-seed gate (ADR-0005 + ADR-0008). The goal's success oracle is
    trustworthy iff a seeded success signal exists OR >=2 different success types from
    >=2 distinct sources agree. Same-type repeats and same-source multi-type evidence
    never satisfy this."""
    seeded = any(s.is_seeded and _stable(s) for s in success)
    return seeded or independent_diverse(success)


def summary_corroborated(summary: SignalSummary,
                         peers: list[SignalSummary]) -> bool:
    """Per-signal corroboration (ADR-0029, refines ADR-0005 + ADR-0008).

    True iff THIS success summary itself participates in genuine
    independent-diverse evidence: there exists another STABLE success summary of
    a DIFFERENT `type` such that, taken together with this summary, the two span
    >=2 DISTINCT source ids. That is, the corroborating evidence is of a
    different evidence type AND comes from at least one source other than this
    summary's own source(s).

    This is what stops a lone single-agent summary from borrowing the GOAL-LEVEL
    `independent_diverse` flag (the seeds set it True) and riding to `believed`
    without any different-type, different-source signal corroborating IT
    (defect B, the `create-welcome-popup` inflation). It deliberately PRESERVES:
      - the positive control (two types from two sources: each summary has a
        different-type partner from a different source);
      - the ADR-0008 INHERENT boundary (a seed of one type + a single agent of a
        DIFFERENT type: the agent summary's different-type partner is the seed,
        whose source is a second distinct source) - one genuine corroborating
        observation on a seed stays believed.
    A STREAM of same-type single-agent paraphrases has NO different-type partner,
    so none of them is corroborated; the believed set stays the seeds only.

    `summary` is assumed already stable (the caller checks `mostly_present` and
    non-oscillation); `peers` are the fresh success summaries for the goal.
    """
    if not _stable(summary):
        return False
    for other in peers:
        if other is summary:
            continue
        if other.type == summary.type:
            continue
        if not _stable(other):
            continue
        if len(summary.source_ids | other.source_ids) >= 2:
            return True
    return False


def is_stale(summary: SignalSummary, *, now: datetime, current_version: str | None = None,
             config: TrustConfig | None = None) -> bool:
    """Public staleness check. A stale signal (aged out, or only seen under other app
    versions) must not lend its type/source to corroborating FRESH claims — otherwise
    an old seed could prop up brand-new assertions across releases."""
    return _is_stale(summary, now, current_version, config or TrustConfig())


def _is_stale(summary: SignalSummary, now: datetime, current_version: str | None,
              config: TrustConfig) -> bool:
    if summary.last_verified is None:
        return False
    age = now - summary.last_verified
    if age > timedelta(days=config.stale_after_days):
        return True
    if current_version is not None and summary.observed_app_versions:
        # Seen, but never under the version we are testing now → likely stale.
        if current_version not in summary.observed_app_versions:
            return True
    return False


def classify(summary: SignalSummary, *, oracle_independent: bool, agreeing: set[SignalType],
             now: datetime, current_version: str | None = None,
             config: TrustConfig | None = None,
             peers: list[SignalSummary] | None = None) -> Status:
    """Assign a Status to one signal. Precedence (most→least severe):
    quarantined > contested(contradiction) > stale > believed > contested(uncorroborated).

    `oracle_independent` is `independent_diverse(success_signals)` for this goal
    (>=2 types from >=2 sources, ADR-0008); `agreeing` is
    `agreeing_types(success_signals)`. They let a per-signal decision respect the
    goal-level rule without recomputing it per call.

    `peers` are the goal's FRESH success summaries (the same set the goal-level
    flag was computed over). They drive the ADR-0029 per-signal corroboration
    check: an agent summary is promoted only when IT itself participates in
    genuine independent-diverse evidence, never by borrowing the goal-level flag
    alone. When `peers` is None the caller could not supply the set; the
    promotion then falls back to the goal-level flag (the legacy behavior),
    which the projection no longer relies on.
    """
    cfg = config or TrustConfig()

    if is_flip_flop(summary.presence):
        return Status.QUARANTINED
    if has_contradiction(summary.presence):
        return Status.CONTESTED
    if _is_stale(summary, now, current_version, cfg):
        return Status.STALE

    # Seeded (human/spec) success/failure signals are trusted from cold-start.
    if summary.is_seeded and summary.mostly_present:
        return Status.BELIEVED

    # Agent-observed: believed only when THIS signal itself participates in
    # genuine corroboration (ADR-0029) - a different-type partner from a
    # different source - not by riding the GOAL-LEVEL independence flag alone. A
    # lone same-type single-agent summary stays `contested`, never promoted,
    # even when the goal has a seeded independent-diverse oracle. When `peers`
    # are supplied (the projection always supplies them) the per-signal check is
    # authoritative; without them we fall back to the goal-level flag so callers
    # that classify a signal in isolation keep the prior behavior.
    if summary.mostly_present and summary.type in agreeing and len(agreeing) >= 2:
        if peers is not None:
            if summary_corroborated(summary, peers):
                return Status.BELIEVED
        elif oracle_independent:
            return Status.BELIEVED

    return Status.CONTESTED


def confidence_of(summary: SignalSummary, *, now: datetime,
                  config: TrustConfig | None = None) -> float:
    """Confidence in one signal: rises with same-type observation_count and falls
    with age. Same-type repeats raise confidence WITHIN the signal but never grant
    independence (ADR-0005). Seeded signals get a floor — they are trusted.

    count_factor = 1 - 0.5**positives   (1→0.5, 2→0.75, 3→0.875, …; saturating)
    recency      = 0.5**(age_days / half_life_days)
    """
    cfg = config or TrustConfig()
    positives = summary.positives
    if positives == 0:
        base = 0.0
    else:
        count_factor = 1.0 - 0.5 ** positives
        if summary.last_verified is not None:
            age_days = max(0.0, (now - summary.last_verified).total_seconds() / 86400.0)
            recency = 0.5 ** (age_days / cfg.half_life_days)
        else:
            recency = 1.0
        base = count_factor * recency

    if summary.is_seeded:
        floor = summary.seed_confidence if summary.seed_confidence is not None \
            else cfg.seed_confidence_floor
        base = max(base, floor)

    return round(min(1.0, max(0.0, base)), 4)
