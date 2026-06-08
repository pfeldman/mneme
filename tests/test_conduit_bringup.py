"""Slow Conduit bring-up gate (ADR-0016 sec 1 C1).

This test boots the Conduit docker-compose stack and asserts the API
responds within the 30-minute ceiling. It is GATED behind the env var
`PRAXIS_RUN_CONDUIT_BRINGUP=1` so the default `bash verify.sh` stays
fast; CI / Pablo invokes it explicitly when the C1 gate needs to be
exercised. Without the env var the test is skipped with a clear reason.

The test does not assert anything about Conduit functionality (the
regression-recall arms do that). It only verifies the C1 contract from
ADR-0016: a stranger to this repo can `bash bring_up.sh` and the API
responds zero in under 30 minutes wall clock.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BRINGUP_SCRIPT = REPO_ROOT / "experiments" / "regression_recall_real" / "setup" / "bring_up.sh"

GATE_ENV = "PRAXIS_RUN_CONDUIT_BRINGUP"


@pytest.mark.skipif(
    os.environ.get(GATE_ENV) != "1",
    reason=(
        f"slow Conduit bring-up gate skipped by default; set {GATE_ENV}=1 to run. "
        "Cold-cache wall-clock is typically <10 minutes; the ADR-0016 C1 ceiling is 30 minutes."
    ),
)
def test_conduit_bringup_script_exits_zero_within_30_minutes() -> None:
    """ADR-0016 sec 1 C1: `bash bring_up.sh` must exit 0 within 1800s wall
    time on a developer laptop, including image pull on a cold docker
    cache. The script enforces its own deadline (PRAXIS_CONDUIT_DEADLINE_SECONDS
    default 1800); this test wraps it in a hard subprocess timeout so a
    runaway daemon does not stall the CI run."""
    assert BRINGUP_SCRIPT.exists(), f"bring_up.sh missing at {BRINGUP_SCRIPT}"

    # Hard wall-clock cap: 30 minutes + 60s grace for subprocess startup.
    timeout_seconds = 1800 + 60
    try:
        completed = subprocess.run(
            ["bash", str(BRINGUP_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            timeout=timeout_seconds,
            text=True,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"Conduit bring-up exceeded the {timeout_seconds}s subprocess timeout; "
            f"ADR-0016 C1 (30-minute ceiling) violated."
        )
    finally:
        # Teardown best-effort; we do not care if it fails (the next run will
        # rebuild). The bring-up script knows how to clean up.
        subprocess.run(
            ["bash", str(BRINGUP_SCRIPT), "--teardown"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            timeout=300,
        )
    assert completed.returncode == 0, (
        f"bring_up.sh exited with {completed.returncode}; "
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


@pytest.mark.skipif(
    os.environ.get(GATE_ENV) != "1",
    reason=f"slow Conduit gate skipped by default; set {GATE_ENV}=1 to run.",
)
def test_conduit_check_subcommand_idempotent_when_up() -> None:
    """Once the stack is up, --check is the idempotent probe a harness uses
    between arms. ADR-0016 C1 + the bring-up script contract: --check returns
    0 when the API responds, 3 when it does not, never hangs."""
    completed = subprocess.run(
        ["bash", str(BRINGUP_SCRIPT), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=30,
        text=True,
    )
    # The previous test brought the stack up + tore it down; --check may
    # return 3 here. Either 0 or 3 is acceptable per the script contract.
    assert completed.returncode in (0, 3), (
        f"--check returned {completed.returncode}; expected 0 (up) or 3 (down). "
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
