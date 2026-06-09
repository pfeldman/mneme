# ADR-0031: Signals as structured checks for relational and after-action facts

Status: Accepted (2026-06-09)

## Context

ADR-0030 made a signal a CHECKABLE FACT by letting its `value` carry a
`value_predicate`: an English template whose text outside a `{slot}` is an
INVARIANT matched by containment and whose declared slots are per-run instance
tokens tolerated on presence/shape only. That fixed the `create-welcome-popup`
false REGRESSED: the matcher now compares the FACT (a route, a banner, a 2xx
plus an id), not the phrasing, and the goal passes live.

Two failure modes survive ADR-0030, because a `value_predicate` is still ENGLISH
the agent must reproduce and the matcher still STRING-matches it:

1. A LONG invariant is flaky run-to-run. A short invariant (a route prefix, a
   one-line banner substring) matches reliably; the agent reproduces it almost
   verbatim. A long sentence ("GET ... returns 2xx and the campaign list
   contains a row whose id equals {id}") is paraphrased differently each run, so
   containment sometimes misses on words the agent dropped or reordered. The
   invariant is a fact, but it is carried as a phrase, and a phrase is fragile.

2. A RELATIONAL or aggregate fact cannot be expressed as invariant+slot AT ALL.
   This is the load-bearing gap and it was hit live. The goal `delete-a-campaign`
   has a believed success signal:

     "A fresh server-rendered load of the Current Campaigns list after archiving
      returns exactly one fewer campaign and no longer includes the archived
      campaign id, confirming the removal persisted server-side."

   "exactly one fewer" is a COUNT DELTA: a comparison between a BEFORE state and
   an AFTER state. "no longer includes the archived id" is an AFTER-ACTION
   ABSENCE of a per-run identifier. Neither is a fixed phrase. There is no
   invariant string to put outside a slot, so `value_predicate` cannot represent
   it, and the free-text Jaccard path string-matches a relation against prose and
   misses. The signal stays unconfirmed; with no UNCERTAIN bucket in the
   break-vs-drift model (ADR-0023), `classify_goal` maps the unconfirmed success
   signal to REGRESSED. The run came back a FALSE REGRESSED ("the app broke, file
   a bug") on a goal whose archive flow worked perfectly: the list really did go
   from N to N-1 and the id really was gone.

The root cause generalizes ADR-0030's own root cause one level further. ADR-0030
said: a fact whose match depends on word-overlap with a phrasing is a coordinate
in phrasing-space; make it a predicate hard on the invariant. But a predicate is
STILL a phrasing for facts that are relations (a delta, a membership change, a
status code). The fix is to let a signal be evaluated as a TRUE STRUCTURED
ASSERTION over structured OBSERVATION DATA, not as a string the matcher
string-matches. The agent reports the raw data it observed (it counted 15 rows
before, 14 after; the archived id is not in the after-set); the body evaluates
the assertion programmatically (14 - 15 == -1; the id is absent). No string
comparison, no Jaccard, no containment.

This is the third tier of an additive progression, each tier stricter and more
structured than the last, each one opt-in per signal:

- free-text `value` + Jaccard floor (ADR-0028) - the legacy path, prose vs prose.
- `value_predicate` - a string invariant with slots, matched by containment
  (ADR-0030). Right for a fact that IS a stable phrase with per-run instance
  tokens (a route, a banner).
- `check` (this ADR) - a typed structured assertion evaluated over structured
  observation data. Right for a fact that is a RELATION (a count delta) or an
  AFTER-ACTION state (an absence) that no phrase can carry.

The machinery to do this already exists and constrains the design.
`Risk.trigger` is a typed discriminated union today (`HttpTrigger` /
`SequenceTrigger`), validated at the write boundary, rendered deterministically
into the prompt. This ADR gives a signal's `value` the same kind of typed,
validated, write-boundary-checked structure the risk's `trigger` already has,
and reuses the variable-slot convention ADR-0030 established for per-run
identifiers.

## Decision

### 1. A signal gains an optional typed `check`: a small discriminated union of structured assertions, evaluated programmatically over structured observation data.

`Signal` gains an optional `check` field: a discriminated union of a SMALL,
fixed set of typed assertion kinds, exactly mirroring how `Risk.trigger` is a
discriminated union of `HttpTrigger` / `SequenceTrigger`. A signal with a `check`
is matched by EVALUATING the assertion against the structured data the agent
observed, never by string-matching a value or a predicate.

`check` is the third, PREFERRED path for a fact that is relational or
after-action. It is additive: `value` stays required (it is the projection
grouping key and the human-readable description), and a signal with no `check`
and no `value_predicate` is matched exactly as today.

### 2. The check vocabulary is the SMALLEST set that fixes `delete-a-campaign`: `list_count_delta` and `element_membership`. Everything else is deferred.

Mirroring the conservative `trigger_validator` discipline (a tiny banned-phrase
set, not an open grammar), this ADR ships exactly the two kinds the live failure
requires, and no more:

- `list_count_delta` - the RELATIONAL primitive. Declares an expected integer
  delta (e.g. `expect_delta: -1`). The agent observes a BEFORE count and an
  AFTER count; the body checks `after - before == expect_delta`. This is what
  "exactly one fewer" needs.
- `element_membership` - the AFTER-ACTION primitive. Declares an identifier slot
  (the per-run archived id, `{campaign_id}`) and an expected state
  (`expect: absent` or `expect: present`). The agent observes whether that
  identifier is in the observed set after the action; the body checks the
  observed membership equals the expected one. This is what "no longer includes
  the archived id" needs.

The `delete-a-campaign` network signal is re-seeded as TWO structured signals
(one `list_count_delta` of -1, one `element_membership` absent of the archived
id), each independently checkable, replacing the single compound prose signal.

Deferred, explicitly NOT shipped here: `http_status`, `url_matches` (regex),
`dom_text_contains`. The first two have no consumer in `delete-a-campaign`, and
`dom_text_contains` is already covered by an ADR-0030 `value_predicate` (the
archive-confirmation dialog "(ID: {id})" stays a `value_predicate`, unchanged).
Adding them now would be vocabulary for its own sake. They are a future ADR if a
real SUT need appears (the same posture ADR-0030 took on richer slot shapes).

### 3. The agent self-reports the before AND after in ONE structured observation; the runner does NOT capture a separate baseline (decision A).

A relational fact needs a BEFORE baseline. Two ways were on the table: (A) the
agent self-reports both the before and the after in one observation, the body
computes the relation; (B) the runner captures a separate before-baseline by
driving the browser ahead of the action. This ADR takes (A).

The agent already observes the list before and after the archive during a normal
run; reporting both numbers it saw adds no browser control flow. Option (B)
would require the runner to drive a pre-action browse, which breaks the ADR-0027
contract that the executor is ONE opaque call the runner cannot interrupt or
sequence. (A) keeps the executor protocol intact (one dict in, one dict out) and
keeps the runtime-agnostic body (ADR-0003): the body computes the relation from
grounded numbers, it never touches a browser.

The structured observation rides on `ObservedSignal` as an optional, typed
`observed` payload keyed by check kind:

- `list_count_delta` -> `{before_count: int, after_count: int}`.
- `element_membership` -> `{identifier: str, present: bool}` (the per-run
  identifier the agent saw, and whether it was present in the after-set).

The brain reports WHAT IT OBSERVED (the two counts, the identifier and its
membership), filling the slot with the real per-run id. The body evaluates the
assertion deterministically and `verdict_from_observations` computes the verdict
(ADR-0019, ADR-0028, ADR-0030). "The check holds" is a COMPUTED result over the
observation, never a boolean the brain asserts.

### 4. The matcher gains a `check` branch BEFORE the predicate and Jaccard branches; exact-type equality still gates first.

`_value_matches` dispatches on the TARGET signal, in this order:

- exact-type equality (ADR-0028) gates first, unchanged and never relaxed: the
  observation's `type` must equal the signal's declared `SignalType`. A
  structured check never loosens the type guard.
- TARGET has a `check` -> evaluate the check against the observation's structured
  `observed` payload (decision 3). No predicate, no Jaccard.
- else TARGET has a `value_predicate` -> evaluate the predicate (ADR-0030).
- else -> Jaccard over `_tokens` (the legacy free-text path, ADR-0028).

A check is evaluated by a pure module (`model/check.py`, mirroring
`model/predicate.py`): zero runtime/browser deps (ADR-0003), the same code the
write-boundary validator uses, so the validator and the matcher can never drift
on what a valid check is or what it means.

### 5. The hard guardrail: a structured check is STRICTER than every string path and can NEVER admit a false PASS.

This is the non-negotiable (AGENTS.md 5, docs/06). The structured path tightens,
never loosens:

- A `list_count_delta` whose observed `after - before` does NOT equal
  `expect_delta` is a NON-match. A planted regression where archive does not
  remove (after == before, delta 0) cannot satisfy an `expect_delta: -1`: it is
  a loud non-match -> UNCERTAIN -> REGRESSED, exactly the loud signal the product
  exists to keep.
- An `element_membership` whose observed membership does NOT equal the expected
  state is a NON-match. A planted regression where the archived id is STILL
  present cannot satisfy `expect: absent`.
- A MISSING or MALFORMED structured observation is a NON-match, never a free
  pass: no `observed` payload, a missing `before_count`, a non-integer count, an
  empty identifier -> the check does not hold. An un-reportable check fails
  CLOSED (loud), it does not fall through to a looser path.
- The body computes the relation; the agent never self-certifies it. The agent's
  two numbers are raw observation data under the same grounding contract as every
  other observation (ADR-0028: grounded in evidence, never fabricated to complete
  the list).

The only axis that "loosens" relative to a string match is the one ADR-0030
already identified: the per-run instance token (the real id, the real counts) is
not compared literally between seed and run. Everything structural (the delta,
the membership, the presence of both observed counts) is checked exactly.

### 6. A check is validated at the write boundary; a malformed check is a loud rejection, never a silent downgrade.

Mirroring `trigger_validator.validate_trigger` and the ADR-0030 predicate
validator, a `check` is validated when the signal is authored (pydantic at the
model boundary):

- a `list_count_delta` with a non-integer or absent `expect_delta` is REJECTED.
- an `element_membership` with an empty / malformed identifier slot, or an
  `expect` that is not `present` / `absent`, is REJECTED.
- an unknown check kind is REJECTED by the discriminated union.

Rejection is a loud pydantic / boundary error, never a silent downgrade to the
predicate or free-text path. A seed that declares a structured check must produce
a valid one or fail at authoring time, the same way a free-text risk trigger and
an ADR-0030 predicate fail at write time.

### 7. The `check` field threads through the projection read path, or it is silently dead.

A seed-authored `check` is per-signal metadata an agent does not observe, so (like
`value_predicate`) it is lost in the observation->believed projection rebuild and
must be RESTORED onto the projected believed signal by matching back to its seed
signal on `(type, value)`. ADR-0030's `_restore_predicates` is the precedent;
this ADR generalizes that restore to carry `check` alongside `value_predicate`.
Without this the read path returns `check=None`, the matcher silently falls back
to the looser path, and the structured fact is dead end to end. A read-path test
(`project_with_seed` -> `check` survives) is part of the definition of done for
this change, not just an in-memory model test. This is called out as its own
decision because it is the exact hard-won failure mode of the prior session: a
field added to the model and matcher but dropped by the projection is invisibly
non-functional in a live run.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Make the runner drive a separate before-baseline browse. Decision A keeps the
  ADR-0027 one-opaque-call executor contract intact and the body
  runtime-agnostic (ADR-0003). The before count is self-reported by the agent and
  the body computes the relation. A runner-captured baseline is a future ADR only
  if self-report proves untrustworthy in practice.
- Let the agent self-certify the relation or the verdict. The agent reports raw
  observed data (before/after counts, identifier + membership); the body
  evaluates the assertion and computes the verdict (ADR-0019, ADR-0028, ADR-0030).
  "The check holds" is a computed result, not a brain assertion.
- Make any part of a check fuzzy. The delta is checked with integer equality, the
  membership with boolean equality. There is no Jaccard, no containment, no
  threshold anywhere in the structured path. A missing or malformed observation
  fails CLOSED.
- Loosen the Jaccard floor (ADR-0028) or the predicate path (ADR-0030) to "fix"
  the relational case. The check is a new, stricter, additive path; the two
  legacy paths stay exactly as strict as they are today.
- Store the bound instance token (the real campaign id, the real counts) as
  durable knowledge. The observed counts and identifier are per-run observation
  data, redacted at the adapter boundary like any other observation; the seed
  stores only the abstract check (the expected delta, the slot name), never a
  concrete run's numbers (the same posture ADR-0017 / ADR-0030 take).
- Grow the check vocabulary beyond `list_count_delta` and `element_membership`.
  `http_status`, `url_matches`, `dom_text_contains` are deferred (decision 2);
  richer kinds are a future ADR if a real SUT need appears.
- Bulk re-seed existing dogfood seeds into the structured form. The free-text and
  predicate paths keep matching them (decisions 4); re-seeding a signal into a
  `check` is a separate, explicit, per-signal human (teach) action.
- Activate the deferred `states` or `paths` fields (ADR-0009 sec 2, ADR-0011).
  Structured checks live ENTIRELY inside the existing `Signal` shape (a new
  optional `check` field and a new optional `observed` payload on the
  observation); they introduce no state graph and no path graph and give neither
  deferred field a consumer. They stay deferred.

## Consequences

Positive:

- The `delete-a-campaign` false REGRESSED becomes a real PASS at its root: the
  relational signal ("exactly one fewer", "archived id absent") is now a
  structured assertion the body evaluates from grounded counts, not a relation
  string-matched against prose. A genuinely-passing archive flow stops reading as
  "the app broke".
- Relational and after-action facts become first-class. The product can now
  express a count delta and a membership change, the two fact shapes that no
  amount of phrasing could carry, with no false PASS exposure.
- Matching gets STRICTER on the structured axis: an integer-equality delta check
  rejects a no-op archive that a prose match could have coincidentally admitted.
- The change is additive and reuses a proven pattern (the `trigger` discriminated
  union, the write-boundary validator, the ADR-0030 slot convention and restore
  path). No new architectural concept is invented.

Negative:

- A THIRD matching path (free-text / predicate / check) now coexists. This is
  intentional and tiered, but it is three code paths in `_value_matches` to keep
  correct and tested.
- A structured check is more work to author than prose or a predicate: the human
  (teach) must pick the kind, the expected delta or membership, and the
  identifier slot. The validator rejects the worst cases; the free-text and
  predicate paths remain for facts that are not relational.
- The before count is SELF-REPORTED by the agent (decision A). The body computes
  the relation, so the agent cannot self-certify "it passed", but it does supply
  the two numbers. Mitigation: the same grounding contract as every observation
  (ADR-0028), the relation is computed not asserted, the structured path is
  stricter than Jaccard, and a dedicated no-false-PASS test gate (a planted
  regression must reach FAIL/UNCERTAIN, never PASS) guards it. If self-report
  proves untrustworthy live, the runner-captured baseline (option B) is the
  documented next step.
- The check vocabulary is deliberately tiny (two kinds). A fact whose shape is
  not a count delta or a membership change falls back to a `value_predicate` or
  free text, which is still available and still strictly ordered behind the check
  path.

Invariants respected:

- `oracle-sacred` / `no-false-pass` (ADR-0005, docs/06, AGENTS.md 5): the check is
  evaluated with exact integer / boolean equality, stricter than every string
  path; a missing or malformed observation fails CLOSED; the agent never
  self-certifies the verdict; a dedicated no-false-PASS gate covers the planted
  regression. No path admits a false PASS.
- `invariants-not-coordinates` (AGENTS.md 1): a signal becomes an explicit typed
  assertion over observed data, the furthest possible thing from a
  phrasing-coordinate. No selector, xpath, or coordinate becomes representable.
- `provenance-and-confidence-mandatory` (ADR-0004): the `check` rides on the
  existing `Signal`, which keeps mandatory provenance, confidence, and status;
  the `observed` payload rides on `ObservedSignal`, whose provenance is stamped
  by the system (ADR-0008), not the agent.
- `runtime-agnostic-core` (ADR-0003): the check parser/evaluator is a pure stdlib
  module with zero browser deps; the body computes the relation without driving a
  browser (decision A preserves the one-opaque-call executor of ADR-0027).
- `loud-and-traceable-over-silent-and-convenient` (docs/06): a malformed check
  fails loudly at write time; an un-reportable or unsatisfied check is a loud
  non-OK (never a silent green); the per-run counts are never promoted to durable
  knowledge.
- `no-secrets-tokens-pii-in-knowledge` (ADR-0017, ADR-0026): the seed stores only
  the abstract check; the bound identifier and counts are per-run observation
  data redacted at the boundary.

Invariants this ADR does NOT cover:

- The richer check vocabulary beyond `list_count_delta` / `element_membership`
  (`http_status`, regex `url_matches`, `dom_text_contains` as a check):
  deferred to a future ADR if a real SUT need appears.
- The runner-captured before-baseline (option B): explicitly NOT taken (decision
  3); documented as the next step only if self-report proves untrustworthy.
- The teach-skill authoring UX for writing a structured check (how the human is
  prompted to pick a kind and name the identifier slot): an implementation
  concern for the teach skill (ADR-0022), out of scope here.
- The aggregate verdict LABEL for an unconfirmed-but-not-failed goal: owned by
  ADR-0023's taxonomy. This ADR removes a class of false UNCERTAIN at its source
  (the matcher) but does not relabel the UNCERTAIN-to-REGRESSED branch.
- Activation of `states` / `paths`: explicitly NOT touched; they stay deferred
  (ADR-0009 sec 2, ADR-0011).

## Relation to prior ADRs

Builds on ADR-0030 (signals as checkable facts with explicit variable slots,
Accepted): adds the THIRD, stricter tier above `value_predicate`. ADR-0030
matched a string invariant by containment; this ADR evaluates a typed assertion
over structured data for facts that are relations (a count delta) or after-action
states (an absence) that no string invariant can carry. The dispatch order is
check -> predicate -> Jaccard; the predicate and Jaccard paths are untouched.

Extends ADR-0009 (Phase-1 R-mode verdict and the `risks.trigger` structured form,
Accepted): generalizes the typed-discriminated-union-with-write-time-validation
pattern from `risks.trigger` to a `success/failure` signal's `check`, reusing the
variable-slot convention for per-run identifiers. The verdict rule (failure ->
FAIL, all believed success matched -> PASS, otherwise UNCERTAIN) is unchanged;
only WHAT counts as "matched" gains a structured path.

Upholds ADR-0028 (regress agent confirms every believed success signal in its
declared type, Accepted): the exact-type equality gate is unchanged and gates
before the check branch; a structured check never relaxes the type guard. ADR-0028
made the agent report in the RIGHT TYPE, ADR-0030 made the matcher compare the
RIGHT FACT, ADR-0031 makes the matcher evaluate the RIGHT RELATION.

Preserves ADR-0027 (self-contained console runner, one-opaque-call executor,
Accepted): decision A keeps the executor a single dict-in/dict-out call; the agent
self-reports the before baseline rather than the runner driving a pre-action
browse. The runtime-agnostic body (ADR-0003) computes the relation without a
browser.

Builds on ADR-0019 (brain-agnostic body, Accepted), ADR-0023 (the regress
verdict, Accepted), and ADR-0029 (agent observations cannot self-certify the
oracle, Accepted): the check is evaluated by the BODY from grounded observations,
never self-certified by the brain; a goal whose check does not hold stays a loud
non-OK routed by the ADR-0023 taxonomy; regress still does not grow the believed
set from a confirmation run.

Upholds ADR-0017 (auth_state abstract posture, Accepted) and ADR-0026 (session is
a secret, Proposed): the bound identifier and the observed counts are per-run
observation data redacted at the adapter boundary, never durable knowledge; the
seed records only the abstract check. Does not supersede any prior ADR.
