"""UI mutation injector. Perturbs the app so a recorded script breaks but the
goal is unchanged. The point is to test step REGENERATION, not selector healing.

Each mutation changes HOW you reach the goal, never WHETHER the goal is
achievable. Mutations are held as global active state that `simapp` consults when
it builds a flow's current control layout, so a single process can flip the app
between baseline and mutated without restarting anything.
"""
from __future__ import annotations

from enum import Enum


class Mutation(str, Enum):
    RENAME_CONTROL = "rename_control"        # "Sign in" -> "Log in"
    MOVE_FIELD = "move_field"                # reorder email/password
    SWAP_LABEL = "swap_email_for_username"   # email field becomes username
    INSERT_STEP = "insert_intermediate_step" # add a "continue" interstitial


_active: set[Mutation] = set()


def apply(mutation: Mutation) -> None:
    """Apply one mutation to the running test app."""
    _active.add(mutation)


def reset() -> None:
    """Revert to the unmutated baseline."""
    _active.clear()


def active() -> frozenset[Mutation]:
    """The mutations currently in effect (read by simapp)."""
    return frozenset(_active)
