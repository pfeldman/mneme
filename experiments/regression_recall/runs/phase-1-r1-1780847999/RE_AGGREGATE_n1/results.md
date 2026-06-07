# Phase 1 regression-recall results - release phase-1-r1

Budget per arm per goal: **5000 tokens**

## Arm aggregates

| arm | n_seeds | recall | knowledge-visible | stale-trap | false_pos | off_path |
|-----|---------|--------|--------------------|------------|-----------|----------|
| `cold` | 1 | 0.12+/-0.00 | 0.00+/-0.00 | 0.00 | 0.89+/-0.00 | 0.63 |
| `cold_readme` | 1 | 0.25+/-0.00 | 0.20+/-0.00 | 0.00 | 0.50+/-0.00 | 0.62 |
| `memory` | 1 | 0.75+/-0.00 | 0.80+/-0.00 | 1.00 | 0.40+/-0.00 | 0.66 |

## Kill gates

| gate | passed | detail |
|------|--------|--------|
| `overall_recall` | PASS | delta=0.500, sigma=0.000, memory.recall=0.750+/-0.000, cold_readme.recall=0.250+/-0.000 |
| `knowledge_visible_recall` | PASS | delta=0.600, sigma=0.000, memory=0.800+/-0.000, cold_readme=0.200+/-0.000, categories=['knowledge_visible'] |
| `false_positive_guardrail` | PASS | fp_delta=-0.100, sigma=0.000, memory_fp=0.400+/-0.000 |
| `false_pass_control` | PASS | false_pass_rate=0.000 |
| `stale_trap_recall` | PASS | stale_trap_recall=1.000 |
| `off_path_fraction` | PASS | off_path_fraction_mean=0.659 |

## Verdict: **CONTINUE**

All gates passed; the moat survives this experiment run.
Phase 1 continues; ADR-0010 records the verdict.
