# Candidate persistence (ADR-0014)

When an exploring agent pokes at your app and guesses a new risk (for example, "I think submitting checkout twice creates two orders"), Praxis saves that guess to the knowledge store with the label `contested`. A `contested` guess shows up in the human review queue and is never treated as trusted. It only flips to `believed` (Praxis-speak for "we rely on this") when a second, independent source agrees: either a human writes a matching entry into the spec, or a second agent with a different identity reports the same thing in a different form. Until then, the guess is visible but quarantined.

## Why this exists

Before this feature, an exploring agent could surface interesting hunches inside a single run, but those hunches died with the run. The next session started blind. We also did not want the opposite extreme: an agent that says "this is broken" three times in a row and gets believed by sheer repetition. Candidate persistence is the middle path. Hunches survive across sessions so a reviewer can act on them, but a single agent talking to itself cannot self-promote. The same independence rule that already gates trusted signals (ADR-0005, ADR-0008) is reused here so we do not invent a second, weaker bar.

## How to use it

Most of this runs by itself. When you invoke `praxis explore`, the runner asks the agent for new risk guesses and new open questions, and the adapter writes them as `CandidateEvent` records into the knowledge store. You do not call a "save candidate" command by hand.

To see what is queued up for review:

```bash
praxis review
```

Output groups by goal and lists every `contested` candidate with its description, trigger, the set of sources that have weighed in, and how many supporting events exist. Limit it to one goal with `--goal <goal_id>`.

To promote a candidate, do NOT edit the candidate event. Open the goal's `*.knowledge.yaml` and add a matching `risks:` (or `uncertainties:`) entry with the same `id`, sourced from a human/spec. The next projection sees seed + candidate as two independent sources and flips status to `believed`.

## A worked example

You run `praxis explore` on the `checkout` goal. The agent posts a candidate risk:

```
id: idempotency
description: POST /orders with same Idempotency-Key creates two orders
trigger: sequence, "submit checkout with same Idempotency-Key"
provenance: agent-A
```

Run `praxis review`:

```
## checkout
  [contested candidate_risk / sequence] idempotency: POST /orders with same Idempotency-Key creates two orders
     trigger: 2x submit checkout with same Idempotency-Key  expect: two distinct order_ids returned
     confidence=0.70  sources={agent-A}  events=1
```

You agree the risk is real. You add a matching entry to `checkout.knowledge.yaml` under `risks:` with `id: idempotency`, an HTTP-shaped trigger (`POST /orders`), and `source_type: human`. Next `praxis review`: the risk no longer shows. It is now `believed`, backed by two independent sources (`spec-1` and `agent-A`) and two evidence kinds (`http` and `sequence`).

## What it does NOT do

It does not let an agent vote itself into the trusted set. Two writes from the same `agent_identity` count as one source and stay `contested`, no matter how many times the agent repeats itself.

It does not accept free-text triggers. A risk whose trigger is "fails under high load" or "flaky behaviour" is rejected at the adapter boundary and never reaches the store. Triggers must be the structured HTTP or sequence shapes from ADR-0009.

It does not auto-delete unresolved candidates. They decay to `stale` via an explicit decay event (ADR-0013) once the recency window passes. Nothing is silently dropped.

It does not edit candidates in place. Promotion always appends a new seed event; the original candidate stays as-is for the audit trail.

## How to verify it works for you

Run the candidate persistence test suite:

```bash
pytest tests/test_candidate_persistence.py -v
```

You should see green for: single-source candidates staying `contested`, seed-plus-candidate promoting to `believed`, two writes from the same agent identity NOT promoting, and banned phrases like "flaky behaviour" being rejected at the adapter.

To exercise the review surface against your own project, run `praxis explore --goal <your_goal>` followed by `praxis review`. Any agent-proposed risk should appear in the contested queue with its provenance.

## Reference

- ADR-0014: E-mode candidate persistence as sibling CandidateEvent (`docs/adr/0014-e-mode-candidate-persistence.md`)
- ADR-0009 section 4: structured trigger validator (the rule that rejects free-text)
- ADR-0008: source-independence rule applied to candidate promotion
- ADR-0013: decay path for unresolved candidates
