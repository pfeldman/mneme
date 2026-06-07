"""Adversarial harness for the multi-writer concurrency contract (ADR-0012).

The harness lives under `experiments/` (not under `tests/`) because ADR-0012
section 4 names it as the load-bearing assurance for the multi-writer module
and binds it to the same commit as the store changes; it is a first-class
artifact, not an internal pytest. The pytest entry in `tests/test_multi_
writer_harness.py` invokes `run_all()` so the harness runs under `bash verify
.sh` and would fail merge if any scenario regresses.
"""
