# Multi-writer concurrency (ADR-0012)

Praxis lets several QA agents append notes to the same shared memory at the same time. With this feature, no note is lost if two agents write at once, and no group of identical agents can fake agreement to promote a bad finding into "believed" knowledge. The shared memory stays append-only: every observation lands as its own file, and the official answer ("is this fact believed, contested, or quarantined?") is recomputed from the full set of files every time you read.

## Why this exists

Without a multi-writer contract, two failure modes are easy to ship by accident.

- **Lost notes.** Two agents write at the same millisecond, one note overwrites the other, and the team never sees the second observation.
- **Self-corroboration.** Ten copies of the same agent all report "logout works" and the system treats that as ten independent sources, promoting a single opinion to fact. This is the silent poisoning case: nothing looks wrong from the outside.

Multi-writer closes both. Concurrent writes use the filesystem rename as the commit point (lock-free), and identical agents are folded into one source before any promotion gate runs.

## How to use it

You do not invoke it directly. It is built into the store that every `praxis` command uses. Each event lands as its own file under `<store_root>/<tenant_id>/events/`, so concurrent writers never collide. The tenant id defaults to `local`. If you want to scope a deployment, pass `default_tenant_id` when constructing the store:

```python
from praxis.store import FileEventStore
store = FileEventStore(".praxis/events", default_tenant_id="team-alpha")
```

Two rules the store enforces, so you do not have to remember them at every call site:

- `source_id` is the agent identity (`model::prompt_lineage`), never a per-process token like `pid`, `session_id`, `run_uuid`, or hostname. Use `source_id_for(model=..., prompt_lineage=...)` to build it; the constructor `AgentIdentity` rejects bad shapes.
- A read scoped to tenant A never returns events from tenant B. There is no cross-tenant read API. A tenant id with a path-traversal character (`..`, `/`, `\`, null byte) is rejected at the boundary.

The same writes work whether one agent or twenty agents are running against the same store root; no extra flag, no extra lock, no migration step.

To observe the contract in action, run the adversarial harness:

```bash
python experiments/multi_writer/harness.py
```

## A worked example

Six parallel QA agents, all running the same model (`claude-sonnet`) with the same prompt lineage (`r-mode-v1`), each append twelve copies of the same success observation ("logout action becomes available") for goal `g`. That is 72 events landing concurrently against the same store root.

After all threads join:

- `store.read("g")` returns exactly 72 events. Nothing was lost to the race; every writer's file landed under `<root>/local/events/` with a unique name.
- The projection (the computed "what do we believe?" view of the events) marks the `logout` signal as `contested`, not `believed`. Because all 72 events share one `source_id` (`claude-sonnet::r-mode-v1`), the diversity gate sees one source, not 72. Promotion is structurally impossible.

Now swap one of the six writers for an agent with a different `source_id` (a different model or prompt lineage) reporting a different signal type, for example a network signal `POST /session returns 2xx`. The projection now sees two distinct sources bringing two distinct evidence types and promotes the goal to `believed`. Diversity is real, not counted.

Contrast with the racing-contradiction case: two distinct agents race on the same failure signal, one reports `present=True`, one reports `present=False`. The projection surfaces `contested` (not last-write-wins), so the disagreement stays visible to whoever reads next.

## What it does NOT do

- It does not guarantee security isolation between tenants. The tenant path prefix is a placeholder against accidental cross-scope reads; it is not RBAC. Phase 3 replaces it with a real permission boundary.
- It does not merge agreeing observations into one "consensus" event. Each writer's observation persists as its own row with its own `source_id`; the projection counts sources at read time.
- It does not solve recency or staleness collisions on `observed_app_version`. That collision is out of scope here and will be addressed in a separate ADR.
- It does not provide a cross-tenant read API. A read across tenant ids is not representable on the store SPI by design.
- It does not pack events into a single log file. Phase 2 ships one JSON file per event; a packed-segment backend is a Phase 3 option behind the same SPI.

## How to verify it works for you

Run the adversarial harness and the unit tests for the contract:

```bash
python experiments/multi_writer/harness.py
pytest tests/test_multi_writer.py tests/test_multi_writer_harness.py
```

The harness prints a PASS/FAIL banner for five scenarios: concurrent same-source (no false promote), concurrent diverse-source (legitimate promotion), racing contradiction (stays `contested`), racing oscillation (becomes `quarantined`), and partial-write failure (leftover `.tmp` ignored). All five must pass. The full verify gate is `bash verify.sh`.

## Reference

- ADR-0012: `docs/adr/0012-multi-writer-concurrency-contract.md` (the formal contract: store-layer, gate-layer, single-tenant placeholder, day-one harness).
- Related: ADR-0001 (append-only event log), ADR-0005 (oracle trust by diversity), ADR-0008 (type-diversity needs source-independence).
