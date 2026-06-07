"""Phase 2 exploration reward experiment package (ADR-0015).

Reward is observability-only. The formula lives in
`src/praxis/metrics/exploration_reward.py`; this package provides the
harness-side wrapper that consumes a run's projection and renders the
per-arm report. The pre-registration + adversarial review live in this
directory as markdown.
"""
