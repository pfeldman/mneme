"""Loader + types for the pre-registered regression-recall manifest.

The manifest is the ground truth: which regressions are planted, what their
category is, what observation an arm would emit to be counted as "detected".
Sealed before any arm runs (docs/phase-1-experiment.md). This module reads
the JSON and exposes typed access; it does not mutate the manifest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Category = Literal["tourist", "knowledge_visible", "stale_trap"]
Kind = Literal["success", "failure"]


@dataclass(frozen=True)
class Regression:
    """One planted regression. Hashable so callers can put it in sets."""

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
    testapp_baseline_sha: str
    regressions: tuple[Regression, ...]

    def by_slug(self) -> dict[str, Regression]:
        return {r.slug: r for r in self.regressions}

    def by_category(self) -> dict[Category, tuple[Regression, ...]]:
        out: dict[Category, list[Regression]] = {}
        for r in self.regressions:
            out.setdefault(r.category, []).append(r)
        return {c: tuple(rs) for c, rs in out.items()}


def load_manifest(path: str | Path) -> Manifest:
    raw = json.loads(Path(path).read_text())
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
    return Manifest(
        schema_version=raw["schema_version"],
        release=raw["release"],
        testapp_baseline_sha=raw["testapp_baseline_sha"],
        regressions=regressions,
    )


_DEFAULT_PATH = Path(__file__).parent / "manifest.json"


def default_manifest() -> Manifest:
    return load_manifest(_DEFAULT_PATH)
