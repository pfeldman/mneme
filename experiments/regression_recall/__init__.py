"""Phase 1 regression-recall experiment package.

Pre-registered design in `docs/phase-1-experiment.md`. Manifest at
`manifest.json` is sealed before any arm runs; metrics + kill gates live in
`metrics.py`; the harness (offline plumbing + the live protocol entry) is
sketched but the actual live runs follow `LOCAL_RUN.md` (subscription path)
or the API-key alternative.
"""
