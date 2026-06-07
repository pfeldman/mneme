#!/usr/bin/env bash
# One-command verification for Mneme Phase 0.
# Creates a venv, installs declared deps, and runs tests + ruff + mypy + the
# offline experiment harness, then prints a PASS/FAIL summary.
#
#   bash verify.sh
#
# Exit code is non-zero if any check fails. Makes no changes to tracked files.
set -uo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"
fail=0
declare -a SUMMARY

step() {  # step "label" cmd...
  local label="$1"; shift
  echo ""
  echo "==================== $label ===================="
  if "$@"; then
    SUMMARY+=("PASS  $label")
  else
    SUMMARY+=("FAIL  $label")
    fail=1
  fi
}

# 1. venv + deps
if [ ! -d "$VENV" ]; then
  echo "Creating venv at $VENV ..."
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "Installing dependencies (pip install -e '.[dev]') ..."
pip install --quiet --upgrade pip >/dev/null
if pip install --quiet -e ".[dev]"; then
  SUMMARY+=("PASS  install deps")
else
  SUMMARY+=("FAIL  install deps"); fail=1
fi

# Run tools through the venv interpreter so they always see the installed deps.
step "pytest"            python -m pytest -q
step "ruff"              python -m ruff check .
step "mypy"             python -m mypy
step "offline harness"   python experiments/ui-mutation/harness.py
step "oracle stress"     python experiments/ui-mutation/oracle_stress.py

echo ""
echo "############################ SUMMARY ############################"
for line in "${SUMMARY[@]}"; do echo "  $line"; done
echo "#################################################################"
if [ "$fail" -eq 0 ]; then
  echo "ALL GREEN — build is sane. (Harness numbers are from the simulator; see"
  echo "experiments/ui-mutation/README.md for why they validate the machinery,"
  echo "not the thesis.)"
else
  echo "SOME CHECKS FAILED — see the sections above."
fi
exit "$fail"
