# Mneme

> A shared **semantic-memory layer for QA agents.**
> Codename — rename freely (`praxis` → your brand) in `pyproject.toml` and `src/`.

Most testing tools store **procedures** (click A, fill B, assert C). Mneme stores
**knowledge about the system under test** — goals, how to recognize states, what
success and failure actually look like, which alternative paths exist, and which
risks lurk — and keeps that knowledge **decoupled from the steps** any single run
happened to use.

Agents read the knowledge to attempt a goal, **regenerate their own steps**, and
write back what they observed. Over time the memory becomes a living model of the
app, maintained by agents instead of by hand.

```yaml
goal: A returning user can establish an authenticated session.
success_signals:
  - a logout action becomes available          # behavioral, durable
  - POST /session returns 2xx + session cookie  # network, durable
alternative_paths: [email-password, social-oauth]
known_risks:
  - captcha (trigger: several consecutive failures)
  - mfa     (trigger: account has MFA / new device)
```

## Why this is not "another test framework"
This is **model-based testing reborn**: the discipline that failed historically
because maintaining the model by hand cost more than the tests it replaced.
The bet here is that **agents can build and maintain the model themselves**,
which inverts that economics. The procedure is disposable; the knowledge is the asset.

## What's in this repo
- `docs/` — the full design: vision, architecture, schema, MVP experiment, risks, roadmap.
- `docs/adr/` — the load-bearing decisions and why.
- `schema/` — the language-neutral knowledge schema (JSON Schema) + real examples.
- `src/praxis/` — package skeleton (model, store, merge, oracle, adapters).
- `experiments/ui-mutation/` — the one experiment that validates or kills the idea.

## Start here
1. `docs/00-product-brief.md` — the one-page pitch.
2. `AGENTS.md` — how Claude Code should build this (non-negotiables included).
3. `experiments/ui-mutation/README.md` — build this first.

## Non-negotiables (the spine of the design)
1. Store **invariants, not coordinates**.
2. Every assertion carries **provenance + confidence** (ADR-0004).
3. The store is **append-only** (ADR-0001) — no overwrite of knowledge.
4. Core stays **runtime-agnostic**; runtime code lives behind adapters (ADR-0003).
5. The **oracle is sacred** — a success oracle is believed only via evidence
   diversity (≥2 different signal types) or a human/spec seed, never by counting
   agents; the first oracle is seeded (ADR-0005). Silent poisoning is the way this
   product dies (docs/06).

## Running in CI

Wire `praxis regress` into your own CI and gate on its exit code (ADR-0024). A
REGRESSED, ERROR, or AUTH-EXPIRED goal exits non-zero and fails the job; full
walkthrough and an example workflow in [docs/examples/ci.md](docs/examples/ci.md).

**LOUD NOTE: a STALE-only run exits 0, so a gate that reads ONLY the exit code
silently passes drifted knowledge.** By design STALE does not fail the run (the
app changed on purpose; the fix is a human re-seed, not a red gate), so when every
non-OK goal is STALE the process exits `0`. A CI that gates on the exit code alone
goes green while the knowledge drifts out of date. If you care about drift, do not
gate on the exit code alone: read the report and act on STALE (fail or warn on the
`STALE` count in `regress-aggregate.md`, or on `skipped > 0` in the JUnit XML
`regress-aggregate.xml`, or open a re-teach follow-up). The exit code tells you the
app did not break; it does not tell you the knowledge is still accurate.

License: Apache-2.0 (recommended).
