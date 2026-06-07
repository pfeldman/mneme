"""Loader + types for the pre-registered Phase-2 real-app manifest.

The Phase-2 manifest (`manifest.json` in this package) mirrors the Phase-1
shape (`experiments/regression_recall/manifest.py`) but adds an SUT block
(Conduit details from ADR-0016) and a goal slate with `auth_state_after_success`
projections (ADR-0017).

This module reads + types the JSON; it does not mutate the manifest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Category = Literal["tourist", "knowledge_visible", "stale_trap"]
Kind = Literal["success", "failure"]


@dataclass(frozen=True)
class AuthState:
    """Projected auth posture for a goal (per ADR-0017).

    Mirrors `praxis.model.AuthState` at the manifest layer (separate dataclass
    so the manifest file stays loadable without the pydantic model installed;
    keeps the experiment package data-only).
    """

    authenticated: bool
    scope: str | None


@dataclass(frozen=True)
class Goal:
    goal_id: str
    description: str
    auth_state_after_success: AuthState


@dataclass(frozen=True)
class Sut:
    name: str
    source: str
    license: str
    backend_url: str
    frontend_url: str
    compose_file: str
    selection_adr: str


@dataclass(frozen=True)
class Regression:
    slug: str
    category: Category
    description: str
    goal_id: str
    plant_endpoint: str
    expected_observation: str
    expected_kind: Kind
    expected_signal_type: str


@dataclass(frozen=True)
class Manifest:
    schema_version: str
    release: str
    sut: Sut
    goal_slate_adr: str
    schema_extension_adr: str
    goals: tuple[Goal, ...]
    regressions: tuple[Regression, ...]

    def by_slug(self) -> dict[str, Regression]:
        return {r.slug: r for r in self.regressions}

    def by_category(self) -> dict[Category, tuple[Regression, ...]]:
        out: dict[Category, list[Regression]] = {}
        for r in self.regressions:
            out.setdefault(r.category, []).append(r)
        return {c: tuple(rs) for c, rs in out.items()}

    def goal_ids(self) -> tuple[str, ...]:
        return tuple(g.goal_id for g in self.goals)


def _load_auth_state(raw: dict[str, object]) -> AuthState:
    return AuthState(
        authenticated=bool(raw["authenticated"]),
        scope=raw["scope"] if raw["scope"] is not None else None,  # type: ignore[arg-type]
    )


def load_manifest(path: str | Path) -> Manifest:
    raw = json.loads(Path(path).read_text())
    sut_raw = raw["sut"]
    sut = Sut(
        name=sut_raw["name"],
        source=sut_raw["source"],
        license=sut_raw["license"],
        backend_url=sut_raw["backend_url"],
        frontend_url=sut_raw["frontend_url"],
        compose_file=sut_raw["compose_file"],
        selection_adr=sut_raw["selection_adr"],
    )
    goals = tuple(
        Goal(
            goal_id=g["goal_id"],
            description=g["description"],
            auth_state_after_success=_load_auth_state(g["auth_state_after_success"]),
        )
        for g in raw["goals"]
    )
    regressions = tuple(
        Regression(
            slug=r["slug"],
            category=r["category"],
            description=r["description"],
            goal_id=r["goal_id"],
            plant_endpoint=r["plant_endpoint"],
            expected_observation=r["expected_observation"],
            expected_kind=r["expected_kind"],
            expected_signal_type=r["expected_signal_type"],
        )
        for r in raw["regressions"]
    )
    slugs = [r.slug for r in regressions]
    if len(slugs) != len(set(slugs)):
        raise ValueError(f"manifest has duplicate slugs: {slugs}")
    goal_ids = {g.goal_id for g in goals}
    for r in regressions:
        if r.goal_id not in goal_ids:
            raise ValueError(
                f"manifest regression {r.slug!r} targets unknown goal "
                f"{r.goal_id!r} (not in goal slate {sorted(goal_ids)})"
            )
    return Manifest(
        schema_version=raw["schema_version"],
        release=raw["release"],
        sut=sut,
        goal_slate_adr=raw["goal_slate_adr"],
        schema_extension_adr=raw["schema_extension_adr"],
        goals=goals,
        regressions=regressions,
    )


_DEFAULT_PATH = Path(__file__).parent / "manifest.json"


def default_manifest() -> Manifest:
    return load_manifest(_DEFAULT_PATH)
