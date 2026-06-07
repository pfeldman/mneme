"""Pytest bootstrap: make `mneme` (src layout) and the hyphen-named experiment
package importable without an editable install.

The repo root itself is also added so dotted imports like
`experiments.multi_writer.harness` resolve from the regular `experiments/`
package (the multi-writer harness lives in a hyphen-free path on purpose so
the standard import works under pytest)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT, ROOT / "src", ROOT / "experiments" / "ui-mutation"):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)
