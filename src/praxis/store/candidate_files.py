"""Committed candidate tree: one YAML file per observation (ADR-0021 decision 4).

ADR-0021 makes `.praxis/candidates/` the contested-candidate store that travels
between teammates by `git pull` / `git push`. Each agent-proposed candidate risk
or uncertainty (an ADR-0014 `CandidateEvent`) is its own file under a per-goal
subdirectory, named by its observation event id (the ADR-0012 content-addressable
event id), NEVER a row in a shared mutable list:

    .praxis/candidates/<goal>/<observation_event_id>.yaml

This module is the bridge between that committed YAML tree and the in-memory
`CandidateEvent` model the projection (`merge.candidates.project_candidates`)
folds. It is intentionally thin: it serializes a `CandidateEvent` to YAML on
write and re-hydrates it on read. It does NO deduplication, NO corroboration,
and NO promotion. Two observations of the SAME finding are two files that share
one structured `trigger`; the judgment that they are the same finding is made at
projection time by grouping on `trigger`, never by a filename collision or by
editing a file (ADR-0021 decision 4).

Why one file per observation event id, not per finding id:

- The observation event id is unique by construction (a uuid4 hex, ADR-0012), so
  two writers - two teammates, two CI runs - adding a candidate for the same goal
  add two DIFFERENT files under the same per-goal directory. Git merges two added
  files in one directory cleanly; there is no shared line both writers edited, so
  there is no text-level merge conflict and no discovery is lost
  (`concurrent-writes-lose-no-knowledge`).
- A single mutable `candidates.yaml` per goal would reintroduce the last-write-wins
  erasure ADR-0001 and ADR-0012 exist to prevent: two writers appending to the
  same list collide on the same lines and one discovery is silently overwritten
  on merge. The forbidden-alternatives clause of ADR-0021 names this explicitly.

The ADR-0012 source-independence contract carries over unchanged: each candidate
file records its own `agent_identity` as `source_id`, so N same-model writers
adding N candidate files still count as ONE source under ADR-0008's
source-independence rule and the diversity-or-seed gate. One file per id gives
merge-safety; `source_id = agent_identity` keeps the safety from becoming a
self-promotion path. None of that lives here; this module only persists and
reloads events. The gate is the projection's job (`merge/candidates.py`,
unchanged).

The committed tree is append-only by the same construction as the event store:
a file is never overwritten (re-writing the same event id raises), and human
promotion appends a NEW seed event to `knowledge/` rather than editing any
candidate file (ADR-0014 sec 4, ADR-0021 decision 3).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .events import CandidateEvent

# Suffix for one committed candidate file. Distinct from the `.knowledge.yaml`
# suffix `knowledge/` uses so a glob over either tree never crosses the other.
CANDIDATE_FILE_SUFFIX: str = ".yaml"

# Characters that must not appear verbatim in a per-goal subdirectory name: they
# would let a goal id escape the candidates root (`..`, separators, NUL). A goal
# id is author-controlled, so the writer refuses an unsafe id loudly rather than
# silently landing a file outside the tree (loud-and-traceable over silent).
_FORBIDDEN_GOAL_TOKENS: tuple[str, ...] = ("/", "\\", "..", "\x00")


def _safe_goal_dir(goal_id: str) -> str:
    """Return the per-goal subdirectory name, refusing path-escaping goal ids.

    ADR-0021 keys candidates under `candidates/<goal>/`. The goal id is the
    directory name; an id containing a path separator or `..` would land the
    file outside the candidates root. This is the candidate-tree analog of the
    `_validate_tenant_id` guard in `file_store.py`: the placeholder convention
    still refuses to construct an escaping path.
    """
    if not isinstance(goal_id, str) or not goal_id:
        raise ValueError("goal_id is required and must be a non-empty string")
    for token in _FORBIDDEN_GOAL_TOKENS:
        if token in goal_id:
            raise ValueError(
                f"goal_id must not contain path-special characters; got {goal_id!r}"
            )
    return goal_id


class CandidateFileStore:
    """Read/write the committed `.praxis/candidates/<goal>/<id>.yaml` tree.

    One file per observation event id (ADR-0012), append-only and merge-safe
    (ADR-0021 decision 4). This store is deliberately NOT an `EventStore`
    subclass: the `EventStore` SPI carries the per-machine append-only event log
    under the gitignored `runs/` tree (the local source of truth), whereas this
    store is the committed, shared, pulled-and-pushed projection. ADR-0021
    decision 2 keeps those two layers distinct on purpose; collapsing them would
    blur the committed-vs-gitignored split.

    Construct it on the `.praxis/candidates/` directory. Reads tolerate a
    missing directory (no candidates yet); writes create the per-goal
    subdirectory lazily on first use.
    """

    def __init__(self, candidates_dir: str | Path) -> None:
        self.candidates_dir = Path(candidates_dir)

    # ---- paths ------------------------------------------------------------

    def _goal_dir(self, goal_id: str) -> Path:
        return self.candidates_dir / _safe_goal_dir(goal_id)

    def _path_for(self, event: CandidateEvent) -> Path:
        # Named by the observation event id, NOT the finding (candidate) id.
        # Two observations of one finding (same candidate_id, same trigger)
        # therefore land in two distinct files; dedup is the projection's job.
        return self._goal_dir(event.goal_id) / f"{event.event_id}{CANDIDATE_FILE_SUFFIX}"

    # ---- write ------------------------------------------------------------

    def write(self, event: CandidateEvent) -> Path:
        """Persist one `CandidateEvent` as its own committed YAML file.

        Append-only (ADR-0001): re-writing the same event id raises rather than
        overwriting. The write goes to a `.tmp` sibling then atomically renames,
        so a reader (or a `git add`) never sees a half-written file; the rename
        is the commit point exactly as in `FileEventStore.append` (ADR-0012).
        Returns the path written.
        """
        goal_dir = self._goal_dir(event.goal_id)
        goal_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(event)
        if path.exists():
            raise FileExistsError(
                f"candidate file already exists, refusing to overwrite: {path}"
            )
        # Serialize the whole event so a re-read reconstructs the same
        # CandidateEvent (event id, ts, agent_identity, payload). sort_keys=False
        # keeps a stable, human-readable key order for the committed file so a
        # `git diff` of a new candidate reads top-to-bottom.
        body = yaml.safe_dump(
            event.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        )
        # Salt the tmp name with the event id so two writers picking the same
        # goal dir cannot stomp each other's tmp file (lock-free concurrency).
        tmp = goal_dir / f".{event.event_id}.tmp"
        tmp.write_text(body, encoding="utf-8")
        tmp.rename(path)
        return path

    def write_all(self, events: list[CandidateEvent]) -> list[Path]:
        """Persist many events, one file each. Returns the paths written."""
        return [self.write(ev) for ev in events]

    # ---- read -------------------------------------------------------------

    def read(self, goal_id: str | None = None) -> list[CandidateEvent]:
        """Re-hydrate committed candidate files into `CandidateEvent`s.

        With `goal_id`, reads one goal's subdirectory; without it, folds across
        every goal subdirectory under `candidates/`. The result is sorted by
        `(ts, event_id)` so two reads of the same committed tree yield the same
        order the projection sees (deterministic fold, ADR-0012). A missing
        candidates directory (or a missing per-goal subdirectory) reads as an
        empty list, never an error: a fresh project simply has no candidates.

        The aggregate read (`goal_id=None`) is what `praxis review` folds into
        the review queue across every goal (ADR-0021 decision 4).
        """
        if goal_id is not None:
            dirs = [self._goal_dir(goal_id)]
        else:
            if not self.candidates_dir.exists():
                return []
            dirs = [d for d in sorted(self.candidates_dir.iterdir()) if d.is_dir()]
        events: list[CandidateEvent] = []
        for d in dirs:
            if not d.exists():
                continue
            for f in sorted(d.glob(f"*{CANDIDATE_FILE_SUFFIX}")):
                events.append(
                    CandidateEvent.model_validate(
                        yaml.safe_load(f.read_text(encoding="utf-8"))
                    )
                )
        events.sort(key=lambda e: (e.ts, e.event_id))
        return events

    def goals(self) -> list[str]:
        """Goal ids that have at least one candidate subdirectory on disk."""
        if not self.candidates_dir.exists():
            return []
        return sorted(
            d.name for d in self.candidates_dir.iterdir() if d.is_dir()
        )
