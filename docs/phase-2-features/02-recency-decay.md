# Recency decay (ADR-0013)

Praxis stores what your QA agents have learned about your app: things like "logout works" or "checkout fails when a coupon is applied". Each piece of knowledge is backed by one or more observations from agent runs. Recency decay is the rule that says: if the evidence backing a piece of knowledge has not been re-confirmed in the last N app versions or T days, that knowledge gets marked "stale" automatically. Every time something flips to stale, Praxis writes a visible audit record (a "decay event") so you can see what aged out, when, and why.

## Why this exists

Without decay, knowledge from app version 1.0 keeps masking what your agents see at 1.4. Example: an agent observed "logout works" at v1.0; six months later the logout flow was rewritten and broke, but the projection (the read-side view that says what is currently believed) still treats the old observations as valid corroboration. Decay closes that hole: old evidence ages out, the "believed" status falls back to "stale", and the next agent run is forced to re-confirm with fresh evidence before the claim is trusted again. The audit event makes the demotion visible instead of silent.

## How to use it

You do not invoke recency decay directly. It runs as part of every projection (every time Praxis reads the event log to decide what is currently believed). The behaviour is controlled by two pre-registered thresholds in `praxis.merge.DecayConfig`:

- `minor_versions_back` (default `2`): an observation is staled when its `observed_app_version` is more than N minor versions behind the current app version. Any major-version bump stales all prior observations for that goal.
- `stale_after_days` (default `90`): an observation is staled when its wall-clock age is more than T days behind run-start. This catches apps that never bump their version string.

Thresholds are pinned per experiment by `praxis_git_sha` in the run manifest. Changing them invalidates prior data (same convention as the Phase 1 kill gates).

The `current_version` (the app version the projection is being read against) is chosen in this order: caller-supplied first (the runner passes it explicitly), otherwise the highest semver-shaped version present in the observation set, otherwise fall through to wall-clock-only decay. Highest-semver is deterministic across concurrent writers, so two agents recording different versions for the same fact cannot make the projection oscillate.

You observe decay in two places:

```bash
praxis status              # summary per goal, "believed" counts reflect post-decay state
praxis review              # surfaces decay events alongside contested observations
```

Under the hood, the projection driver calls `project_with_decay(events, ..., current_version=..., decay_config=...)` which returns the post-decay `KnowledgeFile` and a list of new `DecayEvent`s. The runner appends those events to the store via `FileEventStore.append_decay(...)`. They live in a sibling `decay/` subdirectory next to the observation log.

## A worked example

Day 0, app v1.0.0. Agent A1 observes a behavioural signal ("logout succeeds, then login form reappears"). Agent A2 observes a network signal ("`POST /session` returns 2xx"). Two diverse types, two diverse sources: the projection marks "logout works" as `believed`.

Day 60, app v1.4.0 ships (three minor versions ahead of v1.0.0, which exceeds the default `N=2`). A new regression run starts. The projection re-evaluates the diversity rule over the surviving set:

```
v1.0.0 behavioural obs from A1   -> staled (3 minors back, rule="version")
v1.0.0 network obs from A2       -> staled (3 minors back, rule="version")
surviving set                    -> empty
diversity gate                   -> fails
projected status of "logout"     -> believed -> stale
```

The projection appends a `DecayEvent` with `from_status=believed`, `to_status=stale`, `retired_event_ids=[<A1 obs id>, <A2 obs id>]`, `anchor_current_version="1.4.0"`, `rule="version"`, and a note describing the thresholds. `praxis status` now shows `logout` under `stale`, not `believed`. The next agent run must re-observe `logout` with fresh diverse evidence (two distinct source ids AND two distinct types, all at v1.4.0) before it climbs back to `believed`.

## What it does NOT do

- It does not edit prior events. The event log is append-only; decay never rewrites history. Staled observations stay in the log, marked retired by the decay event that references them.
- It does not silently shift confidence numbers in a way you cannot see. Numeric confidence drift is pure derivation (no event), but every status flip writes a visible event.
- It does not re-promote stale knowledge automatically. If a writer re-asserts the same fact later, that does not un-stale anything; re-promotion only happens when fresh evidence on its own passes the ADR-0008 cold-start gate (two diverse types from two diverse sources).
- It does not let same-type repeats from the same source keep a `believed` status alive. Stacking ten "logout" observations from agent A1 at v1.4.0 does not satisfy the diversity rule once the network signal has aged out.
- It does not flip `contested` (two observations disagreeing) to `believed` just because one side ages out. A contested signal whose live evidence ages out flips to `stale`, never to `believed`.

## How to verify it works for you

Run the decay test suite:

```bash
pytest tests/test_merge_decay.py -v
```

You should see passing tests including:

- `test_decay_then_fresh_signal_restores_via_cold_start_gate`: confirms a stale signal can only come back via fresh diverse evidence.
- `test_decay_does_not_resolve_contested_by_aging_one_side`: confirms `contested` never silently becomes `believed`.
- `test_same_type_repeats_cannot_keep_believed_alive`: confirms diversity rule survives across time.
- `test_decay_event_is_storable`: confirms `DecayEvent`s round-trip through the file store and are append-only (re-appending the same event raises `FileExistsError`).

To see decay fire on your own data: seed a goal, run `praxis regress`, then re-run `praxis status` with a `current_version` that is 3+ minor versions ahead of the seed's `observed_app_version`. The `believed` count for that goal should drop, and a new file should appear under `.praxis/store/decay/<goal-id>/`.

## Reference

- ADR-0013: `docs/adr/0013-recency-decay-as-projection-derivation.md` (the formal contract: thresholds, multi-writer anchor selection, the forbidden alternatives list).
- Related: ADR-0008 (the diversity gate that decay re-runs over the surviving set), ADR-0012 (the multi-writer store decay reads from), ADR-0009 (pre-registration of thresholds).
