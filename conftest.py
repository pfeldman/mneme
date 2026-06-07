"""Pytest bootstrap: make `praxis` (src layout) and the hyphen-named experiment
package importable without an editable install."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT / "src", ROOT / "experiments" / "ui-mutation"):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)
