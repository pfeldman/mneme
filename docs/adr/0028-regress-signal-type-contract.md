# ADR-0028: The regress agent confirms every believed success signal in its declared type

Status: Accepted (2026-06-08)

## Context

R-mode computes a per-run verdict deterministically from what the agent
observed (ADR-0009). `verdict_from_observations` in
`src/praxis/runner/regression.py` returns PASS only when EVERY believed success
signal is matched and no failure fired; otherwise, with no failure, it returns
UNCERTAIN. A believed success signal is matched by `_value_matches`, which
requires two things at once: the observation's `type` must EQUAL the signal's
declared `SignalType` (exact-type equality), and the value strings must share
word-overlap at or above a Jaccard floor (`_PARAPHRASE_THRESHOLD = 0.5`). The
exact-type requirement is deliberate: the five non-negotiables order signal
types by durability (behavioral, network, accessibility, text, url, visual), and
demanding the agent reproduce the SAME evidence type is a guard against "I sort
of saw something".

The regress prompt, however, fights that guard. `render_regression_prompt`
currently tells the agent to type each observation "by what you actually
checked". So when a goal's believed success signal is declared `network` but the
agent confirms the same underlying fact behaviorally or by url, the agent types
its observation as `behavioral`/`url`, `_value_matches` rejects it on the type
mismatch, fewer than all believed success signals match, and the run is
UNCERTAIN even though the fact is true. In the aggregate break-vs-drift model
(ADR-0023) there is no UNCERTAIN bucket: `classify_goal`'s fall-through maps an
UNCERTAIN run (a believed success signal absent, no failure, no healthy
equivalent, not version-behind) to REGRESSED, a FALSE "the app broke, file a
bug".

This was hit live: the goal `create-welcome-popup` (four believed success
signals, one each of behavioral, url, text, network) came back inconclusive
against the real app because the `network` signal was confirmed only
behaviorally and so did not match. The test genuinely passed; the run could not
confirm one signal IN THE TYPE the seed declared.

The fix is to align the agent contract with the matcher, NOT to loosen the
matcher. Loosening (dropping exact-type, lowering the Jaccard floor, or letting
the agent self-assert "I saw it") would admit un-grounded confirmations, which
is the confidently-wrong green that docs/06 names the worst failure mode and
ADR-0005 builds the oracle to prevent. This ADR fixes the prompt-versus-matcher
contract and the teach-time type discipline that follows from it; it does not
change `_value_matches`, the Jaccard floor, or the verdict rule.

## Decision

### 1. The regress prompt names each believed success signal with its declared type and asserts the agent confirm all of them, one grounded observation per signal, each in its declared type.

`render_regression_prompt` lists each believed success signal with its declared
`SignalType` and the fact it asserts (the existing per-signal `[type] value`
rendering), and instructs the agent: there are these success signals; confirm
ALL of them; produce exactly one observation per signal; and emit each
observation IN that signal's declared type, not in a type chosen by what the
agent happened to check. The conflicting instruction to "type the observation by
what you actually checked" is removed, because it directly contradicts the
exact-type equality `_value_matches` enforces and is the source of the false
UNCERTAIN.

### 2. The grounding guardrail is explicit and leads: confirm all, never tick all.

The same prompt states the hard guardrail: each observation must be grounded in
real evidence the agent actually saw, and is NEVER asserted merely to complete
the checklist. If the agent cannot ground a signal in its declared type, it
leaves that signal unconfirmed and does NOT fabricate an observation, because a
false confirmation (a confidently-wrong green) is the worst possible outcome
(docs/06, ADR-0005, AGENTS.md non-negotiable 5). "Confirm every signal" is a
completeness instruction that must never degrade into "tick every signal";
the grounding requirement is stated ahead of the completeness requirement so the
agent reads grounding as the dominant constraint.

### 3. The matcher and the verdict rule are unchanged; the fix is the agent contract.

`_value_matches` keeps exact-type equality and the Jaccard floor
(`_PARAPHRASE_THRESHOLD = 0.5`); `verdict_from_observations` keeps PASS-iff-all-
believed-matched. None of the matching strictness is relaxed. The only change is
what the prompt asks the agent to produce, so a genuinely-passing goal yields
observations that match by construction (right type, grounded value) instead of
being rejected on a type the agent was previously told to choose freely.

### 4. Teach seeds carry only signal types a regress agent can reproduce.

The teach skill guidance gains a corollary: a seed's signal TYPE must be one the
regress browser-agent (the Playwright-driving brain) can actually REPRODUCE on a
later run. A fact that a browser agent can only confirm behaviorally must be
seeded `behavioral`, not `network`, because decision 1 will later demand a
confirmation in the declared type, and a type no agent can produce makes a
genuinely-passing goal come back UNCERTAIN and then falsely REGRESSED. This
constrains WHICH types a seed uses; it does not relax the ADR-0005 / docs/05
diversity requirement that a believed oracle rest on at least two distinct
signal types.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Drop the exact-type equality in `_value_matches`, lower the Jaccard floor, or
  otherwise loosen the matcher so an observation of a different type or thin
  overlap counts as a match. That admits un-grounded confirmations, the
  confidently-wrong green this project exists to prevent (docs/06, ADR-0005).
- Let "confirm every believed success signal" become "assert every signal to
  complete the checklist". An observation with no real evidence behind it is
  forbidden; an unconfirmable signal is left unconfirmed, not fabricated.
- Have the agent self-certify the verdict. The verdict stays computed
  deterministically by `verdict_from_observations` from grounded observations;
  the agent supplies only what it observed (ADR-0009, ADR-0019).
- Silence the result when a signal cannot be confirmed in its declared type. A
  goal with an unconfirmed believed success signal is never a silent OK; it
  stays a loud non-OK (the aggregate routing it gets is owned by ADR-0023 and
  refined separately).

## Consequences

Positive:

- A genuinely-passing goal stops reading as inconclusive (and so stops being
  mapped to a false REGRESSED in the aggregate) just because the agent expressed
  an observation in a different type than the seed declared. The live
  `create-welcome-popup` failure is fixed at its source.
- The fix changes only the agent-facing prompt, so the matcher and the verdict
  rule, the load-bearing guards against a false PASS, are untouched. The change
  cannot make a wrong assertion easier to accept; it makes a right one easier to
  express.
- Teach seeds become more honest: a type the regress agent cannot reproduce is
  flagged at authoring time rather than surfacing as a confusing UNCERTAIN three
  runs later.

Negative:

- The prompt now demands the agent reproduce a specific evidence type per
  signal, which is harder for the agent than free-typing what it saw. A poorly
  chosen seed type (one the agent cannot produce) will still yield an unconfirmed
  signal; decision 4 pushes that cost to teach time, but it does not eliminate
  the possibility of a mis-typed seed.
- A mis-worded prompt that over-emphasizes completeness could push the agent to
  fabricate observations to clear the checklist, the exact false-PASS this ADR
  guards against. The mitigation is that the grounding guardrail leads the
  wording and a test pins both the in-type instruction and the absence of the
  old free-typing clause.

Invariants respected:

- `oracle-sacred` / `no-false-pass` (ADR-0005, docs/06, AGENTS.md non-negotiable
  5): the matcher grounding and the PASS-iff-all-matched rule are unchanged; the
  agent never self-certifies; an unconfirmable signal is left unconfirmed, never
  fabricated.
- `invariants-not-coordinates`: the signal types stay the durability-ordered
  invariants; the change asks the agent to confirm in the declared type, it does
  not introduce selectors or coordinates.
- `loud-and-traceable-over-silent-and-convenient`: a signal that cannot be
  confirmed in its declared type is left unconfirmed and surfaces as a loud
  non-OK; nothing is silently passed to make the run convenient.

Invariants this ADR does NOT cover:

- The aggregate verdict label for an unconfirmed-but-not-failed goal (whether it
  reads as a distinct "could not confirm signal X" rather than a REGRESSED that
  implies the app broke): owned by ADR-0023's taxonomy and refined in a separate
  follow-up. This ADR fixes the agent contract so a genuinely-passing goal does
  not reach that branch; it does not relabel the branch.
- The exact Jaccard floor value and the tokenization in `_tokens`: unchanged
  here; this ADR keeps them as-is and only aligns the prompt to the type they
  already require.

## Relation to prior ADRs

Extends ADR-0009 (Phase-1 R-mode verdict, Accepted): the verdict rule
(failure -> FAIL, all believed success matched -> PASS, otherwise UNCERTAIN) is
unchanged; this ADR aligns the prompt so the agent produces observations that
the existing rule can match, removing a false UNCERTAIN.

Refines ADR-0023 (regress dual surface and the OK / REGRESSED / STALE report,
Accepted): a genuinely-passing goal no longer reaches the UNCERTAIN -> REGRESSED
fall-through in `classify_goal`, so the break-vs-drift verdict stops crying wolf
on a paraphrase/type mismatch. The relabel of that fall-through (a distinct
"could not confirm" outcome) is left to a separate follow-up so this ADR stays
one logical change.

Upholds ADR-0005 (oracle trust by diversity, Accepted) and docs/06 (the
false-PASS asymmetry): the fix deliberately does NOT loosen the matcher, because
a confidently-wrong green is worse than a false UNCERTAIN; the diversity-of-types
requirement for a believed oracle is preserved, and decision 4 only constrains
the seed to reproducible types within that diversity.

Builds on ADR-0019 (brain-agnostic body, Accepted): the change lives in the
agent-facing prompt the brain consumes; the verdict stays computed by the body
from grounded observations, never self-certified by the brain. Does not supersede
any prior ADR.
