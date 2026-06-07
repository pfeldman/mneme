"""Playwright adapter — the BRITTLE recorded-script baseline for the experiment.

This is intentionally the anti-pattern Mneme exists to replace: a procedure
captured once as concrete, coordinate-like steps (selector + action). It does NOT
implement the knowledge SPI (it stores no knowledge). Its only jobs are:

  - `emit()`   : render the recorded steps as a runnable Playwright script (text),
                 to make the baseline concrete and reproducible.
  - represent  : a fixed `list[RecordedStep]` the experiment runs against the test
                 app. When a UI mutation renames/moves/relabels a control, the
                 recorded selector no longer matches and the script breaks LOUDLY —
                 which is the whole point of Measurement 2 (robustness).

`playwright` itself is only needed to execute a real script; `emit()` is pure text,
so this module imports nothing at module load.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecordedStep:
    """One concrete, brittle step. `selector` is exactly the kind of coordinate the
    knowledge schema forbids as durable truth — captured here only as the baseline."""

    action: str  # "goto" | "fill" | "click" | "expect"
    selector: str = ""  # e.g. 'text="Sign in"', '#email' — exact, brittle
    value: str = ""  # text to type, url to visit, or expectation


@dataclass
class RecordedScript:
    """A procedure captured once. The brittle baseline (docs/00, docs/04)."""

    name: str
    steps: list[RecordedStep] = field(default_factory=list)

    def emit(self) -> str:
        """Render the recorded steps as a standalone Playwright (sync API) script."""
        body = [
            "# AUTO-RECORDED Playwright script — the brittle baseline.",
            "# Selectors are exact and break on any UI change (that is the point).",
            "from playwright.sync_api import sync_playwright",
            "",
            "def run(page):",
        ]
        for step in self.steps:
            if step.action == "goto":
                body.append(f"    page.goto({step.value!r})")
            elif step.action == "fill":
                body.append(f"    page.fill({step.selector!r}, {step.value!r})")
            elif step.action == "click":
                body.append(f"    page.click({step.selector!r})")
            elif step.action == "expect":
                body.append(f"    assert page.is_visible({step.selector!r}), {step.value!r}")
            else:  # pragma: no cover - defensive
                body.append(f"    # unknown action: {step.action}")
        body += [
            "",
            "if __name__ == '__main__':",
            "    with sync_playwright() as p:",
            "        browser = p.chromium.launch()",
            "        page = browser.new_page()",
            "        run(page)",
            "        browser.close()",
        ]
        return "\n".join(body)
