# ADR-0033: Enumerated signals are confirmed by identity with mandatory evidence; paraphrase matching is demoted to unsolicited observations

Status: Proposed (2026-06-11)

## Context

A regress run computes its per-goal verdict deterministically (ADR-0009): PASS
when every believed success signal is matched and no failure fired, otherwise
(no failure) UNCERTAIN, and `classify_goal` maps an unexplained UNCERTAIN to
REGRESSED (ADR-0023). A believed signal with no `check` and no
`value_predicate` is matched by the free-text arm of `_value_matches`:
exact-type equality, then word-overlap Jaccard between the observation's
`value` and the seed's `value` at `_PARAPHRASE_THRESHOLD = 0.5`.

This was hit live for the second time (real-app dogfooding, 2026-06-11, a
0.0.3 release blocker). A console regress run came back REGRESSED with BOTH
believed success signals "now absent" while the persisted regress observation
event shows the agent confirmed both facts, `present: true`, in the declared
type, with paraphrased wording:

- behavioral seed "the Digioh Agent panel returns a chat answer that
  identifies the user's account ... rather than an error or a refusal" vs the
  observed "The Digioh Agent panel returned the chat answer 'Your account name
  is Test Account (User ID 46174).', identifying the account by name rather
  than erroring or refusing.": Jaccard 0.400 -> non-match.
- network seed "submitting the question triggers a POST to the chat turn
  endpoint (lightbox-mcpchat-node-dev2.azurewebsites.net/chat/turn) that
  returns a 2xx response" vs the observed "... triggered a POST to
  https://lightbox-mcpchat-node-dev2.azurewebsites.net/chat/turn that returned
  a 200 OK response.": Jaccard 0.409 -> non-match.

Three mechanisms kill the overlap, all measured with the runner's own
tokenizer (`tasks/signal-matching-redesign/analysis.md` holds the full token
sets): verb conjugation with no stemming (returns/returned are disjoint
tokens); honest enrichment with real per-run detail growing the union (the
better the grounding, the lower the score); and the tokenizer treating `/` as
a token character, so a scheme-bearing URL yields `//lightbox` against the
seed's bare `lightbox` (URL-bearing signals are near-guaranteed mismatches).
The prior 0.0.2 run passed the behavioral signal and failed the network one;
0.0.3 failed both; the app was healthy throughout. Free-text matching is
paraphrase roulette.

The same investigation measured what the Jaccard arm actually guards. It does
not guard grounding: an agent that PARROTS the seed value verbatim scores 1.0
(a lazy tick passes for free, today), and an observation whose text literally
describes the regression ("returned an error message refusing to answer")
scores 0.524 against the behavioral seed asserting the opposite, because
negation shares almost all content tokens with the assertion it negates. The
arm rejects true confirmations (five live signals across two incidents) and
admits parrots and contradiction text. It is not an evidence check; it is a
lossy channel between the agent's claim (`kind`, `present`, which the prompt
already lets the agent assert with the seed text in view) and the verdict, and
the loss lands on honest reports. ADR-0028 protected the matcher's strictness
on the premise that loosening it "would admit un-grounded confirmations"; the
measurements falsify that premise for the free-text arm: the strictness was
never blocking un-grounded confirmations, only grounded ones.

Separately, the earlier diagnosis behind ADR-0032 (Proposed) and the teach
streaming guidance - that a streaming endpoint's final status is not
observable on a sampled run - is falsified by the same persisted event: the
agent observed the streamed POST's 200 fine. Matching, not observability, was
the failure both times.

## Decision

### 1. The regress prompt enumerates each believed signal with a stable ref; the envelope confirms by ref, with `present` and a mandatory `evidence` field; the runner binds confirmation to seed by IDENTITY, never by re-matching text.

`render_regression_prompt` already lists every believed success and failure
signal; each line now carries a stable ref token (`S1..Sn` for success,
`F1..Fm` for failure, positional within the same `KnowledgeFile` snapshot the
verdict is computed from). The envelope gains a `confirmations` array:

    {"ref": "S1", "present": true|false, "evidence": "<what was actually
     seen: the concrete text, status, route, count>", ...}

The runner resolves each ref against the same knowledge snapshot it rendered
the prompt from. For an enumerated seed there is NO fuzzy matching: the
binding is the ref, deterministically. The two live signals match by
construction; no threshold is tuned.

### 2. The runner SYSTEM-STAMPS the seed's type and value onto the bound observation; the agent never reproduces seed text.

For a ref-bound confirmation, the recorded `ObservedSignal`'s `type` and
`value` are stamped by the BODY from the seed (the same posture as ADR-0008
provenance stamping: supplied by the system, not the agent). The agent emits
only the ref, the present boolean, the evidence, and (for a `check` target)
the structured `observed` payload. This eliminates the paraphrase roulette and
the parrot channel in one move: there is no agent-authored restatement of the
seed left to compare, and no credit for echoing the prompt. The ADR-0028
declared-type contract is preserved structurally (the bound observation IS in
the declared type) and the agent contract becomes: ground the EVIDENCE in the
declared type's evidence plane.

### 3. Identity replaces only the BINDING; every grounding evaluation still gates after it, fail-closed.

The three tiers keep exactly the deterministic grounding they have today,
applied to the bound confirmation:

- a `check` target (ADR-0031): the confirmation must carry the structured
  `observed` payload and `evaluate_check` still decides, failing closed on a
  missing or malformed payload. A lazy tick without the counts is unconfirmed.
- a `value_predicate` target (ADR-0030): the predicate is evaluated over the
  EVIDENCE string (invariant contained, slots filled and shaped), failing
  closed. A tick whose evidence does not carry the invariant is unconfirmed.
- a free-text target: the evidence must be a non-empty string. There is no
  semantic gate on it (decision 5 records advisory tripwires instead); this is
  the tier decision 6 exists to shrink.

A confirmation that fails its tier's evaluation is VOID: the signal is
unconfirmed, the verdict path is the existing UNCERTAIN routing, and the
report names the void and its reason. Identity-echo can never bypass a
structured evaluation.

### 4. A malformed confirmation is VOID and loud, never a silent green.

Empty or missing `evidence`, an unknown or out-of-range ref, a ref into the
failure list claimed as success (or vice versa), or duplicate refs with
conflicting `present` values: each VOIDS the confirmation. A void leaves the
signal unconfirmed (fail closed), and the `GoalReport.evidence` string names
which confirmations were void and why, so a REGRESSED produced by a sloppy
envelope is distinguishable from a REGRESSED produced by the app, after the
fact, from the report and the persisted regress record alone.

### 5. Advisory grounding tripwires are RECORDED on free-text confirmations, not gating; promoting any tripwire to a gate requires a future ADR with live data.

For each free-text confirmation the body computes and persists (in the
non-promotable `RegressObservationEvent` and the report, ADR-0023 decision 4):

- off-topic flag: evidence-vs-seed token containment (intersection over seed
  tokens) below 0.15. Calibration measured live: both real evidence strings
  score 0.56+, an off-topic string scores 0.0, and a TERSE honest evidence
  string scores 0.235 - which is exactly why the floor is 0.15 and why this is
  a flag and not a gate (a gate tight enough to catch off-topic evidence also
  voids terse honest evidence and recreates the false-REGRESSED class this ADR
  removes).
- parrot flag: the evidence contains zero content tokens beyond the seed's
  (real evidence names per-run concrete detail; a copy of the seed names
  none).
- type-vocabulary flag: the evidence of a `network` confirmation names neither
  a status-shaped nor a URL-shaped token (and analogously per type).

Flags never change a verdict in this ADR. They make a suspicious green LOUD
AND TRACEABLE in the audit record, which is the docs/06 posture, and they
generate the live data a future gating decision needs.

### 6. Teach becomes structured-first: a structurable fact is seeded as a `check` or `value_predicate`; free text is reserved for genuinely unstructurable facts.

The teach skill guidance is inverted from "reach for a check only when the
fact is a relation" to: prefer the structured form whenever the fact is
structurable (a status code, an element presence or absence, a count delta, a
stable phrase with per-run instance tokens), because the structured tiers are
the ones with fail-closed grounding evaluation; free text is the documented
fallback for judgment-shaped facts (the live behavioral signal - "identifies
the account rather than erroring or refusing" - is the canonical example of a
fact that stays free text). The live network signal is the shape of the
`http_status` check kind ADR-0031 deferred "until a real SUT need appears";
that need has now appeared twice (this incident and `create-welcome-popup`),
and adding `http_status` is named as the immediate follow-up ADR, not smuggled
into this one.

### 7. Jaccard survives ONLY for unsolicited observations; a legacy envelope falls back, flagged, for one transition release.

An observation the agent volunteers that is not a ref-bound confirmation
(chiefly: extra failure evidence, the healthy-equivalent support) is matched
exactly as today (type equality, then check / predicate / Jaccard by target).
An envelope that carries no `confirmations` at all (an older brain) is
processed by the full legacy path unchanged, and the report flags that the
goal was matched by paraphrase. The flag makes the remaining roulette visible
instead of silent; the fallback is removed after one release.

### 8. ADR-0032 is withdrawn and the teach streaming guidance is corrected.

The persisted observation event proves the streamed POST's final status WAS
observed (HTTP status and headers arrive before the body streams). On
acceptance of this ADR the maintainer marks ADR-0032 "Withdrawn: premise
falsified by the persisted regress observation event; see ADR-0033". Its open
PARTIAL/STREAMING taxonomy question is moot: the false REGRESSED it tried to
explain was a matching failure this ADR removes, and its own refusal to ship
Option B aged well (a taxonomy change was never needed). The teach SKILL.md
streaming passage is replaced by a narrow caveat: a streaming endpoint's
response STATUS is observable and `network` is a fine type for it; only facts
about the CONTENT of a still-open stream (completion, final payload) belong in
the visible-effect types.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Let the agent self-certify the verdict. A confirmation is a per-signal
  observation (a ref, a boolean, evidence), exactly as `present` already is
  today; the body still computes PASS/FAIL/UNCERTAIN, the failure routing, the
  aggregate classification, the version anchors, and the auth routing
  (ADR-0009, ADR-0019, ADR-0023, ADR-0028).
- Let a confirmation grow the believed set. Confirmations persist only to the
  non-promotable `RegressObservationEvent` stream; `persist_observations`
  stays False for regress (ADR-0029 is untouched).
- Let identity binding bypass a structured evaluation. A `check` or
  `value_predicate` target is confirmed only when its evaluation holds over
  the reported data or evidence, fail-closed (ADR-0030, ADR-0031 unchanged).
- Count a confirmation with empty or missing evidence, an unknown ref, or a
  conflicting duplicate. Each is VOID, loud, and leaves the signal
  unconfirmed; a void is never a green.
- Fix the enumerated-seed path by re-tuning the text channel instead
  (stemming, URL normalization, containment measures, threshold changes). The
  measurements show every such variant admits contradiction text MORE readily
  than today (0.667-0.875 on the probes vs 0.524) and still scores a parrot
  1.0; tuning a channel that cannot discriminate is forbidden as the fix. (The
  same measures ARE used as advisory tripwires in decision 5, where they flag
  instead of gate.)
- Promote a tripwire to a verdict gate inside this ADR's implementation. The
  measured terse-evidence case (0.235) proves a day-one gate misfires; gating
  is a future ADR armed with live flag data.
- Route a tripwire flag to STALE. STALE means the APP changed on purpose
  (ADR-0023); a suspicious evidence string is a reporting concern, not app
  drift, and overloading STALE corrupts its re-seed routing.
- Add a new verdict member or otherwise touch the ADR-0023 / ADR-0026 taxonomy.
  Voids and flags surface through the existing evidence strings and the audit
  record.
- Bulk re-seed existing knowledge files. They keep working unchanged; the
  structured-first guidance applies to new and re-taught seeds (re-seeding is
  the explicit per-signal human act it always was, ADR-0022).

## Consequences

Positive:

- The live release blocker is fixed by construction: both signals bind by ref
  and the goal reads OK; no future paraphrase, conjugation, or URL phrasing
  can flip a verdict on an enumerated seed. The earlier
  `create-welcome-popup`-class misses cannot recur on any tier.
- The verdict channel is cleaner than today on the false-PASS axis it actually
  has: the parrot path (Jaccard 1.0 for echoing the prompt) is closed because
  there is no agent-authored seed restatement left to score; structured tiers
  keep their fail-closed walls; free-text greens become auditable per signal
  (evidence on the record, flags computed) instead of scored-and-forgotten.
- Honest enrichment is rewarded instead of punished: the concrete account
  name, the literal 200, the full URL now land in a persisted evidence field
  a human can read when triaging, instead of lowering a similarity score.
- Knowledge files, the schema, and the verdict taxonomy are untouched; the
  change is confined to the prompt renderer, the envelope parser, the matcher
  dispatch, and the audit record - all additive.

Negative:

- The false-PASS surface on FREE-TEXT signals is now explicitly the agent's
  honesty plus an audit trail, with no deterministic semantic gate at verdict
  time. This is not a regression from today (a parrot already passed for
  free, unaudited) but it is now stated plainly rather than laundered through
  a similarity score that looked like a guard. The mitigation is structural:
  decision 6 shrinks the free-text tier toward facts that genuinely cannot
  fail closed, decision 5 makes suspicious greens loud, and the failure-signal
  channel is unaffected.
- The envelope contract changes (a new array, a new agent obligation). An old
  brain falls back (decision 7), but two parsing paths coexist for one
  release, and the preamble grows.
- Ref binding is positional within a run's knowledge snapshot. The runner
  renders the prompt and computes the verdict from the same in-memory
  `KnowledgeFile`, so skew within a run is impossible, but the persisted
  audit record must stamp the resolved seed value (decision 2) for the record
  to be interpretable after the knowledge file later changes.
- Two more agent-facing fields mean two more ways to emit a malformed
  envelope; the cost is bounded by decision 4 (voids are loud and named,
  never silent).

Invariants respected:

- `oracle-sacred` / `no-false-pass` (ADR-0005, docs/06, AGENTS.md
  non-negotiable 5): no structured evaluation is loosened; everything fails
  closed; the one channel that is removed was measured passing parrots and
  contradiction text while rejecting true confirmations, so its removal
  tightens, not loosens, the real surface. The agent never certifies a
  verdict; the believed set never grows from a confirmation.
- `invariants-not-coordinates` (AGENTS.md 1): a seed is now confirmed as the
  FACT it names, by identity, instead of as a coordinate in phrasing-space
  that the run must land within 0.5 Jaccard of. This ADR finishes what
  ADR-0030 started for the signals ADR-0030 could not cover.
- `provenance-and-confidence-mandatory` (ADR-0004) and ADR-0008 stamping: the
  seed echo on a bound observation is system-stamped like provenance, never
  agent-supplied.
- `loud-and-traceable-over-silent-and-convenient` (docs/06): voids and flags
  are named in the report and persisted in the non-promotable record; a
  suspicious green is visible forever; nothing is silently excused.
- `append-only-store-no-mutation` (ADR-0001): additive event fields only.

Invariants this ADR does NOT cover:

- The `http_status` check kind (and any further check vocabulary): named as
  the immediate follow-up ADR per ADR-0031's deferral discipline, not shipped
  here.
- Promotion of any decision-5 tripwire to a verdict gate: a future ADR with
  live flag data.
- The aggregate verdict LABEL for an unconfirmed-but-not-failed goal (the
  ADR-0023 UNCERTAIN -> REGRESSED fall-through relabel): still owned by
  ADR-0023's taxonomy, unchanged here; this ADR removes the largest remaining
  class of false entries into that branch.
- The LLM-judge escape hatch for borderline free-text evidence: stays the
  deferred Phase 1.5 wiring (ADR-0030 decision 6 posture).

## Relation to prior ADRs

Partially supersedes ADR-0028 (Accepted): decisions 1 (enumerate the believed
signals in the prompt), 2 (grounding leads completeness), and 4 (seed only
reproducible types) survive and are load-bearing here. Decision 3 (the matcher
and Jaccard floor are unchanged; the fix is the agent contract) and the
forbidden alternative "do not loosen the matcher" are superseded FOR
ENUMERATED SEEDS, on measured evidence that the protected mechanism rejected
true confirmations in two live incidents while scoring a verbatim parrot 1.0
and admitting contradiction text at 0.524 - the un-grounded confirmations the
strictness was meant to block were never blocked by it. The supersession is
explicit and evidence-led, not a quiet relaxation.

Builds on ADR-0030 and ADR-0031 (Accepted): their evaluations are promoted to
the per-tier grounding gate applied after identity binding, unchanged in
semantics and strictness; their forbidden alternatives (no fuzz anywhere in a
structured path, fail closed on missing or malformed data) are untouched.
ADR-0028 made the agent report in the right type, ADR-0030 compared the right
fact, ADR-0031 evaluated the right relation; ADR-0033 binds the right SIGNAL.

Upholds ADR-0029 (Accepted): confirmations are non-promotable; regress remains
a read of the believed oracle and a write of a verdict plus an audit record.

Upholds ADR-0005 (Accepted) and docs/06: the false-PASS asymmetry drove every
choice here, including refusing the cheap tokenizer fix because it measurably
widens contradiction admission, and refusing day-one tripwire gates because a
false void is a false REGRESSED.

Refines ADR-0023 (Accepted): no taxonomy change; void confirmations and
tripwire flags ride the existing per-goal evidence strings and the decision-4
audit record.

Withdraws ADR-0032 (Proposed) on acceptance: its premise (streaming final
status unobservable) is falsified by the persisted observation event this ADR
is built on; its open PARTIAL question is moot once the matching failure is
removed. The corrected teach guidance is decision 8.

Extends ADR-0022 (Accepted): teach guidance becomes structured-first; the
human seed act, the no-silent-overwrite rule, and the typed prompts are
unchanged.
