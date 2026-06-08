# Phase 1 regression-recall results - release phase-1-r1

Budget per arm per goal: **5000 tokens**

## Arm aggregates

| arm | n_seeds | recall | knowledge-visible | stale-trap | false_pos | off_path |
|-----|---------|--------|--------------------|------------|-----------|----------|
| `cold` | 1 | 0.00+/-0.00 | 0.00+/-0.00 | 0.00 | 15.00+/-0.00 | 0.57 |
| `cold_readme` | 1 | 0.00+/-0.00 | 0.00+/-0.00 | 0.00 | 9.00+/-0.00 | 0.55 |
| `memory` | 1 | 0.00+/-0.00 | 0.00+/-0.00 | 0.00 | 10.00+/-0.00 | 0.51 |

## Kill gates

| gate | passed | detail |
|------|--------|--------|
| `overall_recall` | **FAIL** | delta=0.000, sigma=0.000, memory.recall=0.000+/-0.000, cold_readme.recall=0.000+/-0.000 |
| `knowledge_visible_recall` | **FAIL** | delta=0.000, sigma=0.000, memory=0.000+/-0.000, cold_readme=0.000+/-0.000, categories=['knowledge_visible'] |
| `false_positive_guardrail` | **FAIL** | fp_delta=1.000, sigma=0.000, memory_fp=10.000+/-0.000 |
| `false_pass_control` | PASS | false_pass_rate=0.000 |
| `stale_trap_recall` | **FAIL** | stale_trap_recall=0.000 |
| `off_path_fraction` | PASS | off_path_fraction_mean=0.508 |

## Verdict: **KILL**

Killed by: overall_recall, knowledge_visible_recall, false_positive_guardrail, stale_trap_recall
