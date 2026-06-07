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

## Rigorous live run — 2026-06-07 (n=15/arm)
Same runtime, now with statistics: **M1 n=15/arm** (5 reps × 3 flows), **M2 n=18/arm**
(3 reps × 6 flow×mutation pairs), 2 guardrail negatives, **68 runs total**. Cost in
browser actions; wall time recorded as an independent second proxy.

**Measurement 1 — existential gate**
| arm | success | cost (actions) | wall (s) |
|-----|---------|----------------|----------|
| memory | 15/15 = 100% | **4.667 ± 0.471** (min 4, max 5) | **31.24 ± 4.76** |
| cold   | 15/15 = 100% | **8.333 ± 0.943** (min 7, max 9) | **51.30 ± 6.73** |

cost ratio 0.56. **Margin = 3.67 actions vs max stdev 0.94 → ~3.9σ separation: the
edge is real, not noise.** Wall time corroborates independently (~39% faster). → **PASS**

**Measurement 2 — robustness**
| arm | recovery | cost / wall |
|-----|----------|-------------|
| memory | **18/18 = 1.00** | 5.0 ± 0.58 actions, 38.3 ± 10.6 s |
| recorded script | **3/18 = 0.17** | 2.93 ± 0.85 s (no LLM) |

Recorded survives only the 3 `swap_email_for_username × login` reps (its
`[name="identifier"]` selector outlives the Email→Username label change). → **PASS**

**Guardrail — oracle correctness (n=68):** false_pass **0.0**, false_fail **0.0** → **PASS**

### Verdict (rigorous)
**CONTINUE.** M1 and M2 are now statistically settled (multi-rep, edge well outside
noise, two agreeing cost proxies). The cost/robustness axes of the thesis hold.

### Caveats (what is settled vs still open)
- ✅ **Reps + variance (was the main caveat): done.** M1 edge is ~3.9σ; wall time
  agrees. Not a fluke.
- ⏳ **Cost in tokens/$ : still a proxy.** Actions and wall time both favor memory,
  but the docs/06 existential risk is framed in tokens/$. One API-key run should
  confirm the margin survives in money, not just actions/time.
- ✅ **Oracle adversarial stress: done offline, gap found AND fixed.**
  `experiments/ui-mutation/oracle_stress.py` injects poisoned event streams into the
  real core. It first surfaced a real vector — a SINGLE source fabricating two
  evidence types self-corroborated to `believed`. Fixed: promotion now requires
  type-diversity AND source-independence (≥2 types from ≥2 sources, ADR-0008).
  Post-fix the oracle RESISTS every attack — lone type; single-source two-types;
  correlated agents up to N=100; contradiction; oscillation; stale — while still
  accepting genuine 2-source/2-type evidence. The one residual (seed + single
  different-type agent → believed) is the INHERENT trust boundary, identical to
  legitimate cold-start corroboration and mitigated temporally, not a bug. Still
  open: a LIVE honesty check (does the agent report absence honestly against a
  deliberately broken app?) once `testapp` grows a break flag.
- ⏳ **Breadth:** one model (Claude Code), one local controlled app, one writer.
  Phase 1 widens flows/apps; Phase 2 adds concurrent writers.

The cost and robustness bets are empirically supported with margin. The remaining
risk is concentrated where docs/06 always said it would be — the oracle. ADR-0007
records the decision to proceed to Phase 1 while that stress test stays on the
critical path.
