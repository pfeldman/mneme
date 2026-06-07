"""Projection of `CandidateEvent`s into reviewable candidate state (ADR-0014).

Phase 1 (ADR-0009) shipped E-mode emitting candidate risks and uncertainties
but had nowhere durable to put them: they survived only inside the in-process
`ExplorationResult`. Phase 2 (ADR-0014) makes them first-class durable events
of a NEW type (`CandidateEvent`) and surfaces them via `praxis review`.

A `ProjectedCandidate` is what `praxis review` and the rest of the system see:
the underlying Risk or Uncertainty, plus the candidate's status under the
diversity-or-seed rule (ADR-0005, ADR-0008), plus all corroborating /
contradicting events for full provenance.

Status rules (ADR-0014 sec 2):
  - Default `contested`. Every CandidateEvent enters as `contested` regardless
    of what the author claimed.
  - Promotion to `believed` requires `independent_diverse(...)` over the
    candidate-event sources + any matching seed: AT LEAST two distinct
    `source_id`s AND at least two distinct evidence types. `source_id =
    agent_identity` (NOT `run_uuid`) per ADR-0009 sec 5 + ADR-0014, so N
    same-model E-mode runs collapse to one source.
  - A `praxis review` reviewer promotes by writing a NEW seed event
    (human/spec source_type). The original CandidateEvent is NEVER edited;
    seed + candidate = two independent sources and the existing gate handles
    promotion (ADR-0014 sec 4).
  - Decay flips contested -> stale via an explicit decay-event append per
    ADR-0013; decay-event append is OUT OF SCOPE for this ADR's first cut
    (the runner does not synthesize decay events; the projection respects
    them when they exist - left for ADR-0013 implementation).

`source_id` for the diversity check: for a candidate risk, the candidate's
own `provenance.source_id` plus any matching seed's `provenance.source_id`.
For a candidate uncertainty, the `raised_by` field of the underlying
Uncertainty plus any matching seed.

`evidence type` for the diversity check on candidate risks: the `trigger.kind`
(`http` vs `sequence`). Two candidates with the SAME `trigger.kind` from two
DIFFERENT sources count as one evidence type under the source-independence
rule (ADR-0008): type diversity AND source diversity are BOTH required. This
is conservative by design - the cost of waiting one extra distinct-type write
to promote is lower than the cost of a same-type self-corroboration loop.

Candidate uncertainties promote by seed corroboration: an uncertainty has no
intrinsic evidence type (it is a question, not a predicate), so the only path
to `believed` is a matching seed Uncertainty + the candidate together, which
is `independent_diverse` by construction (two distinct source_ids, and the
diversity dimension collapses for uncertainties: they are believed-as-open
when a seed and an agent ask the same question).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..model import KnowledgeFile, Risk, Status, Uncertainty
from ..store import CandidateEvent, CandidateRiskPayload, CandidateUncertaintyPayload


@dataclass
class ProjectedCandidate:
    """A candidate after projection: original payload + computed status + sources.

    `status` is ALWAYS one of {`contested`, `believed`}. We deliberately do
    not emit `stale` or `quarantined` here in Phase 2's first cut: ADR-0013
    decay rides on top of this projection by appending explicit decay events
    that, when present, would flip candidates to `stale`; this implementation
    leaves decay-event ingestion as a future hook (ADR-0014 sec 5 notes the
    decay path explicitly but defers the event shape to ADR-0013).

    `corroborating_events` is the full set of CandidateEvents that contributed
    to this candidate's id (sorted by `ts`). `seed_match` is the matching
    Risk or Uncertainty from the seeded KnowledgeFile (if any); a seed-match
    is one source under the independence rule, the candidate is another, and
    together they satisfy `independent_diverse` (ADR-0014 sec 4 promotion).
    """

    candidate_id: str
    candidate_kind: str  # "candidate_risk" | "candidate_uncertainty"
    risk: Risk | None
    uncertainty: Uncertainty | None
    status: Status
    corroborating_events: list[CandidateEvent]
    seed_match: Risk | Uncertainty | None
    distinct_source_ids: set[str]
    distinct_evidence_kinds: set[str]


def _source_ids_for(
    events: list[CandidateEvent],
    seed_match: Risk | Uncertainty | None,
) -> set[str]:
    """Distinct source_ids across candidate events + the seed match (if any).

    For candidate risks: `agent_identity` (which equals `risk.provenance.source_id`).
    For candidate uncertainties: `agent_identity` (which equals `raised_by`).
    A seed contributes its own `provenance.source_id` (risks) or `raised_by`
    (uncertainties).
    """
    sources: set[str] = {ev.agent_identity for ev in events}
    if seed_match is not None:
        if isinstance(seed_match, Risk):
            sources.add(seed_match.provenance.source_id)
        else:
            sources.add(seed_match.raised_by)
    return sources


def _evidence_kinds_for(
    events: list[CandidateEvent],
    seed_match: Risk | Uncertainty | None,
) -> set[str]:
    """Distinct evidence kinds across candidate events + seed match (if any).

    For risks: the `trigger.kind` of each carried Risk. Two candidates with
    the same `trigger.kind` count as one evidence type (ADR-0008: same-type
    repeats are not independence even when sources differ).

    For uncertainties: a fixed dimension `"question"`; uncertainties are
    questions, not predicates, so the diversity check collapses to source
    diversity for them. This is the conservative reading of ADR-0014: a
    candidate uncertainty is `believed-as-open` only when a different
    source (typically a seed) also raises it.
    """
    kinds: set[str] = set()
    for ev in events:
        if isinstance(ev.payload, CandidateRiskPayload):
            kinds.add(ev.payload.risk.trigger.kind)
        else:
            kinds.add("question")
    if seed_match is not None:
        if isinstance(seed_match, Risk):
            kinds.add(seed_match.trigger.kind)
        else:
            kinds.add("question")
    return kinds


def _is_promoted(
    sources: set[str], kinds: set[str], candidate_kind: str,
) -> bool:
    """The independence-and-diversity gate, specialised to candidates.

    Risks: independent_diverse-style >= 2 distinct sources AND >= 2 distinct
    evidence types (here, trigger.kind values).

    Uncertainties: >= 2 distinct sources (seed + candidate or two diverse
    seeds). The "evidence type" dimension collapses for questions, so we
    fall back to source diversity alone for uncertainties - the source-
    independence half of ADR-0008 still binds.
    """
    if candidate_kind == "candidate_uncertainty":
        return len(sources) >= 2
    return len(sources) >= 2 and len(kinds) >= 2


def project_candidates(
    events: list[CandidateEvent],
    *,
    goal_id: str | None = None,
    seed: KnowledgeFile | None = None,
) -> list[ProjectedCandidate]:
    """Project CandidateEvents (+ a seeded KnowledgeFile, if any) into
    projected candidates per ADR-0014.

    Pure: no I/O, no side effects, deterministic given inputs. Stable order
    is (candidate_kind, candidate_id) so two consecutive projections of the
    same store yield byte-identical output for `praxis review`.

    `goal_id` filters; `seed` lets a seeded Risk/Uncertainty with a matching
    id serve as the second independent source for promotion.
    """
    if goal_id is not None:
        events = [ev for ev in events if ev.goal_id == goal_id]

    # Group events by (candidate_kind, candidate_id). Preserve time order
    # within each group so corroborating_events is reproducible.
    groups: dict[tuple[str, str], list[CandidateEvent]] = {}
    for ev in sorted(events, key=lambda e: (e.ts, e.event_id)):
        key = (ev.candidate_kind, ev.candidate_id)
        groups.setdefault(key, []).append(ev)

    seed_risks: dict[str, Risk] = {r.id: r for r in (seed.risks or [])} if seed else {}
    seed_uncertainties: dict[str, Uncertainty] = (
        {u.id: u for u in (seed.uncertainties or [])} if seed else {}
    )

    out: list[ProjectedCandidate] = []
    for (kind, cand_id), evs in sorted(groups.items()):
        # The first event with this id is the canonical payload; later
        # events corroborate (more sources, possibly more trigger kinds).
        # We intentionally do NOT merge fields across events: the original
        # candidate is the assertion; later writes are corroborations of it.
        first = evs[0]

        risk_payload: Risk | None = None
        uncertainty_payload: Uncertainty | None = None
        seed_match: Risk | Uncertainty | None = None

        if isinstance(first.payload, CandidateRiskPayload):
            risk_payload = first.payload.risk
            seed_match = seed_risks.get(cand_id)
        elif isinstance(first.payload, CandidateUncertaintyPayload):
            uncertainty_payload = first.payload.uncertainty
            seed_match = seed_uncertainties.get(cand_id)

        sources = _source_ids_for(evs, seed_match)
        kinds = _evidence_kinds_for(evs, seed_match)
        status = (
            Status.BELIEVED
            if _is_promoted(sources, kinds, kind)
            else Status.CONTESTED
        )

        out.append(
            ProjectedCandidate(
                candidate_id=cand_id,
                candidate_kind=kind,
                risk=risk_payload,
                uncertainty=uncertainty_payload,
                status=status,
                corroborating_events=evs,
                seed_match=seed_match,
                distinct_source_ids=sources,
                distinct_evidence_kinds=kinds,
            )
        )
    return out


def contested_candidates(
    projected: list[ProjectedCandidate],
) -> list[ProjectedCandidate]:
    """Convenience filter: just the `contested` ones, for `praxis review`."""
    return [p for p in projected if p.status == Status.CONTESTED]
