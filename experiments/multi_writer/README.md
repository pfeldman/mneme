# Multi-writer adversarial harness (ADR-0012)

This experiment is the day-one assurance for the multi-writer concurrency
contract. It ships in the same commit as the multi-writer file_store
changes per ADR-0012 section 4 and is wired into `bash verify.sh` so the CI
gate refuses to merge a regression silently.

## What each scenario asserts

| Scenario                   | Property under test                                       |
|----------------------------|-----------------------------------------------------------|
| `concurrent_same_source`   | N writers sharing one `agent_identity` race to append. Zero lost events AND zero false-promote: same-source same-type evidence stays `contested` no matter the count. |
| `concurrent_diverse_source`| Writers across distinct `agent_identity` values bringing different signal types. Zero lost events AND legitimate diversity-or-seed promotion to `believed`. |
| `racing_contradiction`     | Two distinct sources race on a failure signal with disagreeing `present`. The projection surfaces `contested`, not last-write-wins. |
| `racing_oscillation`       | Alternating presence across writers produces `quarantined` per ADR-0005, derived from the event set (no flag mutated on the underlying events). |
| `partial_write_failure`    | A leftover `*.tmp` from a crashed writer (post tmp-write, pre-rename) is ignored by readers; rename is the commit point. |

## Running

Direct:

```
python experiments/multi_writer/harness.py
```

Via the verify gate (recommended):

```
bash verify.sh
```

The pytest wrapper at `tests/test_multi_writer_harness.py` calls `run_all()`
so any new scenario added to the harness automatically participates in the
test suite.

## Why these scenarios live under `experiments/` and not `tests/`

ADR-0012 section 4 makes the harness a first-class delivery artifact: ship in
the same commit, run on every verify, refuse merges that skip it. Mixing
that with the plain pytest tests under `tests/` would hide the contract.
The thin pytest wrapper exists so the harness participates in the regular
test gate, but the scenarios themselves stay here.

## Cross-tenant scope

The harness deliberately does NOT include a cross-tenant write scenario
(ADR-0012 section 3); that lives as a unit test in `tests/test_multi_writer.
py` because tenancy is a constructor / boundary check, not a contention
race. Surfacing it in the harness would dilute the "is the contention path
sound?" question this harness exists to answer.
