"""Bounded-concurrency dispatch for the aggregate runs (ADR-0027 decision 4).

The regress and explore aggregates run one bounded unit of work per goal. This
helper runs those units concurrently up to a cap, the way `pytest-xdist` runs
test files in parallel, while preserving goal ORDER and the per-goal failure
isolation. It depends on nothing in `regression` / `exploration`, so it carries
no import cycle; the callers pass plain callables.

Two invariants the callers rely on:

- `jobs <= 1` is strictly sequential and byte-identical to the pre-ADR-0027
  behavior, so the conservative default (`--jobs 1`) changes nothing.
- `run_one` NEVER raises: it boxes its own per-goal failure into a report
  (a loud ERROR), so a worker thread cannot abort the run and order is always
  recoverable.

Auth-SUBJECT goals are run SERIALLY even when `jobs > 1`: two real logins
against the same test account would collide or trip a login rate limit
(ADR-0027 decision 4), so they never join the concurrent fan-out.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

R = TypeVar("R")

__all__ = ["run_partitioned"]


def run_partitioned(
    goal_ids: list[str],
    run_one: Callable[[str], R],
    *,
    is_subject: Callable[[str], bool],
    jobs: int,
    on_start: Callable[[str], None] | None = None,
    on_done: Callable[[R], None] | None = None,
) -> list[R]:
    """Run each goal's `run_one` and return the reports in `goal_ids` order.

    `jobs <= 1`: strictly sequential. `jobs > 1`: feature / precondition goals
    run concurrently in a `ThreadPoolExecutor(max_workers=jobs)` (threads are
    correct because the per-goal work blocks on a subprocess, releasing the GIL);
    auth-subject goals run serially. `on_done`, when given, is called once per
    completed goal IN THE CALLING THREAD (inside the `as_completed` loop or the
    sequential loop), so a progress callback can print without interleaving
    across worker threads. Completion order drives `on_done`; the returned list
    is always in `goal_ids` order.

    `on_start`, when given, fires once per goal just BEFORE its `run_one`
    (sequential mode: in goal order; concurrent mode: in the worker thread as the
    goal is picked up). It lets a live single-line progress display name the goal
    that is currently running. The rich in-place display only makes sense
    sequentially (one line, one goal at a time); the caller decides whether to
    install it under `jobs > 1`.
    """
    if jobs <= 1:
        out: list[R] = []
        for g in goal_ids:
            if on_start is not None:
                on_start(g)
            r = run_one(g)
            if on_done is not None:
                on_done(r)
            out.append(r)
        return out

    def _run(g: str) -> R:
        if on_start is not None:
            on_start(g)
        return run_one(g)

    subject = [g for g in goal_ids if is_subject(g)]
    concurrent = [g for g in goal_ids if not is_subject(g)]
    by_gid: dict[str, R] = {}
    if concurrent:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_run, g): g for g in concurrent}
            for fut in as_completed(futs):
                r = fut.result()
                by_gid[futs[fut]] = r
                if on_done is not None:
                    on_done(r)
    # Serial pool: no concurrent logins on one test account (decision 4).
    for g in subject:
        r = _run(g)
        by_gid[g] = r
        if on_done is not None:
            on_done(r)
    return [by_gid[g] for g in goal_ids]
