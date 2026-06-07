"""Pytest bootstrap: make `praxis` (src layout) and the experiment dirs
importable without an editable install.

Two flavors:
- `ui-mutation/` (hyphen) uses flat scripts (harness, testapp, runtimes);
  the dir itself is on sys.path so `import harness` works.
- `regression_recall/` (underscore) is a proper Python package; its parent
  `experiments/` is on sys.path so `from regression_recall.manifest import X`
  works.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (
    ROOT / "src",
    ROOT / "experiments",
    ROOT / "experiments" / "ui-mutation",
):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)
