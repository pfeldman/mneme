"""`praxis` CLI surface.

Six verbs: init / learn / regress / explore / review / status. Dispatched
by `main()`; the entry point is wired in `pyproject.toml` as
`[project.scripts] praxis = "praxis.cli:main"`.

Stdlib argparse only (per AGENTS.md: ask before adding deps beyond
pydantic + pyyaml + the Browser Use extra).
"""
from __future__ import annotations

from .main import main

__all__ = ["main"]
