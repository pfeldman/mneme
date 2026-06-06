# Phase-0 results — the existential gate

This is the durable record of the Phase-0 UI-mutation experiment (docs/04). It is
the yes/no on the thesis, with numbers and caveats. See also the live protocol in
`experiments/ui-mutation/LOCAL_RUN.md` and the offline machinery check (`verify.sh`).

## Offline machinery check (simulator)
`python experiments/ui-mutation/harness.py` runs the full protocol against the
deterministic `simapp` stand-in. Its token costs are **stated assumptions, not
measurements**; it validates the harness/metrics/oracle wiring and the kill/continue
logic — **not the thesis**. It clears all three gates by construction.

## First live run — 2026-06-06
Runtime: Claude Code as the browser agent (subscription) via a Playwright MCP, against
the local `testapp.py`. Cost is measured in **browser actions** (a flat-rate
subscription hides per-task tokens). **Single run; small samples** — see caveats.

**Measurement 1 — existential gate (memory vs cold agent)**
| arm | success | avg cost (actions) | per-flow |
|-----|---------|--------------------|----------|
| memory | 100% | **4.67** | login 5, search 4, checkout 5 (14 total) |
| cold   | 100% | **8.33** | login 9, search 7, checkout 9 (25 total) |

cost ratio memory/cold = **0.56** (memory ~44% cheaper, equal reliability) → **PASS**

**Measurement 2 — robustness (memory vs recorded script, 4 UI mutations)**
| arm | recovery |
|-----|----------|
| memory | **6/6 = 1.00** (rename×3, move×1, swap×1, insert×1) |
| recorded script | **1/6 = 0.17** (only `swap_email_for_username` survives — the
recorded script fills by `[name="identifier"]`, not by label) |

→ **PASS**

**Guardrail — oracle correctness (n=20)**
false_pass = **0.0**, false_fail = **0.0** → **PASS**

### Verdict
**CONTINUE** — clears all three gates (cost, robustness, oracle false-pass).

### Caveats (do not over-read this)
- **Single run, small n** (3 flows for M1, 6 flow×mutation pairs for M2, 20 for the
  guardrail). LLM runs are non-deterministic; the rigorous version runs each arm
  ≥5× and reports mean ± stdev. A cost edge inside one stdev of noise is not an edge.
- **Cost = actions, a proxy.** The existential risk in docs/06 is framed in tokens/$
  and time. Actions correlate but are not identical; wall time should be reported too.
- **Recorded-script brittleness depends on selector strategy.** A label-based
  recording would also break on `swap`; a name-based one survives it. The 0.17 is an
  honest artifact of one recording style, not a fixed property.
- **The guardrail was easy** (20 clean runs). Silent poisoning — the failure mode
  that makes shared memory worse than nothing — needs adversarial stress (deliberately
  broken app, contradictory/poisoned observations, stale versions), not clean runs.

This is a strong, encouraging signal that the thesis is **not refuted and has a
measurable margin** — enough to proceed to Phase 1 hardening, not enough to declare
victory. ADR-0007 records the decision.
