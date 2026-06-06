# ADR-0001: Append-only event log is the source of truth

Status: Accepted

## Context
Multiple agents read and write the same memory concurrently. Naive mutable
state leads to lost updates, last-write-wins erasure of knowledge, and silent
poisoning with no way to trace or undo it.

## Decision
Knowledge is never mutated in place. Each agent observation is an immutable
event appended to a log. One file per event (keyed by id), CORAL-style, so
concurrent writers need no locks. The "believed" knowledge state is a
projection derived from the log (see `merge`).

## Consequences
+ Full provenance and auditability; poisoning is traceable and reversible.
+ Lock-free concurrency.
+ Contradictions are preserved (two events disagree) instead of one silently winning.
- Requires a projection step and a compaction/retention strategy at scale.
- Storage grows; mitigate with periodic snapshotting of projections.
