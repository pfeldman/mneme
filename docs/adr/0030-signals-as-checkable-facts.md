# ADR-0030: Signals as checkable facts with explicit variable slots

Status: Proposed

## Context

A regress run computes its per-goal verdict deterministically (ADR-0009): PASS
when every believed success signal is matched and no failure fired, otherwise
(no failure) UNCERTAIN. A believed success signal is "matched" by `_value_matches`
in `src/praxis/runner/regression.py`, which requires two things at once:

- the observation's `type` EQUALS the signal's declared `SignalType` (exact-type
  equality, a deliberate guard, see ADR-0028), AND
- the two `value` strings share word-overlap at or above a Jaccard floor
  (`_PARAPHRASE_THRESHOLD = 0.5`) computed by `_tokens`.

The Jaccard arm is the problem this ADR addresses. A signal's `value` today is
free-text prose ("a logout/sign-out action becomes available", "POST to the
session endpoint returns 2xx and sets a session cookie"). The matcher treats two
prose strings as the same fact when they share half their content words. That is
a coarse heuristic, and it breaks on the legitimate variation between a seed and
a real run.

This was hit live. The goal `create-welcome-popup` has four believed success
signals (one each behavioral, url, text, network). On a real run the agent
confirmed all four facts, IN their declared type, present=True: it really saw
the popup created, the editor route, the banner text, and the create call return
2xx with the new row. But only ONE of four matched. The agent reported the same
facts the seed described, except it reported them with CONCRETE per-run instance
tokens (the real campaign id 329419, the full hostnames `account.digioh.com` and
`hosted.digioh.com`, the real campaign name) while the seed used ABSTRACT
placeholders (a `{id}` placeholder, "campaign list", `/Box/Editor/{id}`). The
extra concrete tokens the agent added, plus the abstract tokens the seed had that
the agent dropped, pushed Jaccard below 0.5 on three of four signals. Per-type
Jaccard that run: behavioral 0.53 (matched), url 0.41, text 0.42, network 0.35
(the three missed). In the aggregate break-vs-drift model (ADR-0023) there is no
UNCERTAIN bucket; `classify_goal`'s fall-through maps that UNCERTAIN run to
REGRESSED: a FALSE "the app broke, file a bug" on a goal that genuinely passed.

The root cause is that prose overlap conflates two different things in one
string: the INVARIANT (the durable fact: a create endpoint returns 2xx; the
route is the editor for the just-created campaign; a banner names the created
campaign) and the per-run INSTANCE (the campaign id, the exact hostname, the
literal name) that changes every run. Jaccard cannot tell them apart: an honest
report that swaps the abstract instance for the real instance reads as "different
prose" and the match fails.

The five non-negotiables already say signals must be invariants, not coordinates
(non-negotiable 1). A fact whose match depends on word-overlap with a particular
phrasing is, in effect, a coordinate in phrasing-space. The fix is to make a
signal a CHECKABLE FACT: a predicate that is HARD on the invariant and TOLERANT
only on declared per-run instance tokens (variable slots). The agent reports
whether the predicate HOLDS (true / false), and the match is exact on the fact,
with no Jaccard.

The machinery to do this already exists in the codebase and constrains the
design. `Risk.trigger.expect` is a structured predicate today; the existing
seeds already use `{username}` and `{tag}` as variable slots in `expect` strings
("GET /api/articles?author={username}", "GET /api/articles?tag={tag} ... includes
the just-published article"). `trigger_validator.py` is the precedent for a
deterministic banned-phrase floor over a predicate plus an LLM-judge escape for
borderline cases. This ADR generalizes that pattern from a risk's `expect` to a
signal's `value`.

## Decision

### 1. A signal's checkable form is a predicate with explicit variable slots: hard on the invariant, tolerant only on declared instance tokens.

A signal gains an optional, structured way to express its `value` as a checkable
predicate. The predicate is a template string containing zero or more named
VARIABLE SLOTS written `{slot_name}`. Everything OUTSIDE a slot is the INVARIANT
and is matched EXACTLY (after case-folding and whitespace normalization, the same
two normalizations the prose path already implies); everything INSIDE a slot is a
per-run instance token the run binds at observation time and the matcher does NOT
compare literally.

Concretely, the three live-failing signals become:

- network: "GET account.digioh.com/ returns 2xx and the campaign list contains a
  row whose id equals {campaign_id}". The method, host, path, the `returns 2xx`,
  and the structural `contains a row whose id equals` are hard; `{campaign_id}`
  is the runtime-bound instance.
- text: "a banner whose text contains 'Created Campaign {campaign_id}'". The
  substring `Created Campaign` is hard; `{campaign_id}` is the variable.
- url: "the route matches /Box/Editor/{numeric}". The literal path prefix is
  hard; `{numeric}` is the variable, optionally typed (see decision 5).

A signal expressed this way is a FACT, not prose: two runs that observe the same
invariant with different instance tokens match, and a run that observes a
DIFFERENT invariant (a non-2xx, a missing row, a wrong route) does NOT match,
regardless of word-overlap.

### 2. Matching a structured signal evaluates the predicate (holds / does not hold); there is no Jaccard for it.

`_value_matches` gains a branch BEFORE the Jaccard arm. When the target signal
carries a structured predicate:

- The exact-type equality from ADR-0028 is unchanged and still gates first: the
  observation's `type` must equal the signal's declared `SignalType`. A
  structured predicate never relaxes the type guard.
- The match succeeds iff the observation satisfies the predicate: the invariant
  text matches exactly (case-folded, whitespace-normalized) and each declared
  variable slot is FILLED by some non-empty instance token in the observation.
  The slot's literal value is NOT compared between seed and run; only its
  PRESENCE (and, with decision 5, its declared shape) is checked.
- Jaccard is NOT computed for a structured signal. The `_PARAPHRASE_THRESHOLD`
  floor applies ONLY to legacy free-text signals (decision 4).

The agent's contribution stays exactly what ADR-0019 and ADR-0028 fixed: the
agent reports what it OBSERVED (kind / type / value, and present=True/False),
filling the slots with the real instance tokens it saw. The verdict stays
computed by `verdict_from_observations` from those grounded observations; the
agent never self-certifies the predicate's truth as a verdict. "The predicate
holds" is a deterministic evaluation the body performs over the observation, not
a boolean the brain asserts.

### 3. The hard guardrail: tolerant ONLY on declared slots; the invariant is matched exactly and a structured match can never be looser than today.

This change must NOT admit a false PASS (a real regression reading as passing).
The tolerance is bounded to the explicitly declared variable slots and nowhere
else:

- Outside a slot, the match is EXACT, which is STRICTER than today's 0.5 Jaccard.
  A run that observed `returns 500` cannot match an invariant that says
  `returns 2xx`; under Jaccard the two strings could share enough other words to
  pass, under exact-invariant matching they cannot.
- Inside a slot, the matcher requires the slot be FILLED (a non-empty instance
  token present in the observation), never that it be absent or arbitrary. An
  empty or missing slot value is a non-match, not a free pass. Decision 5 lets a
  slot additionally declare a SHAPE the filler must satisfy, tightening this
  further.
- A slot may not span the entire predicate. A predicate that is nothing but one
  slot (`{anything}`) carries no invariant and is rejected at validation time
  (decision 6), because it would match everything: exactly the false PASS this
  guards against.

The net effect on strictness: a structured signal is HARDER to match than the
same fact under Jaccard, except on the one axis (the declared instance token)
where Jaccard was producing a FALSE NEGATIVE. The change removes false UNCERTAIN
(and thus false REGRESSED) without opening any path to a false PASS.

### 4. Free-text signals keep the ADR-0028 path unchanged; the structured form is additive and opt-in.

A signal whose `value` is plain prose with no declared slots is matched exactly
as today: exact-type equality plus the `_PARAPHRASE_THRESHOLD = 0.5` Jaccard
floor, unchanged. The structured predicate is a NEW optional capability a seed
opts into, not a replacement that breaks existing seeds. The matcher dispatches
on whether the target signal carries a structured predicate:

- structured target -> evaluate the predicate (decision 2),
- free-text target -> Jaccard over `_tokens` (the legacy path, ADR-0028).

This is the compatibility seam (see Migration): the dogfood seeds that exist
today continue to validate and continue to be matched the old way until they are
re-seeded into the structured form, one signal at a time.

### 5. A slot may declare an optional SHAPE; an undeclared slot is a free instance token.

A variable slot may optionally constrain the instance token it binds, so the
invariant can be tightened where the SUT guarantees a shape. The shape vocabulary
is small and deterministic, mirroring the conservative spirit of the
`trigger_validator` banned-phrase set:

- `{slot:numeric}` - the filler must be all digits (the `/Box/Editor/{id}` case,
  where a non-numeric route segment is itself a regression).
- `{slot:uuid}` - the filler must be a UUID shape.
- `{slot}` (no shape) - any non-empty instance token (the default, decision 3).

The shape is checked against the OBSERVED filler, never compared between seed and
run. This is the only place the matcher inspects a slot's content, and it can
only make a match STRICTER (reject a malformed filler), never looser. The shape
vocabulary is deliberately tiny; richer shapes are a future need, not this ADR's.

### 6. A validator rejects a predicate with no invariant or a malformed slot, loud at write time.

A structured predicate is validated at the adapter boundary, the same place and
the same posture as `trigger_validator.validate_trigger`:

- A predicate that is entirely one slot, or has no non-slot text at all, is
  REJECTED: it carries no invariant and would match everything (decision 3).
- A malformed slot (unbalanced braces, an empty slot name, an unknown shape
  keyword) is REJECTED.
- A predicate whose only non-slot text is stopwords (no durable invariant token)
  is REJECTED, reusing the `_STOPWORDS` set so "the {x}" cannot smuggle past as
  an invariant.

Rejection is a loud pydantic / boundary error, never a silent downgrade to the
free-text path. A seed that declares a structured predicate must produce a valid
one or fail at authoring time, the same way a free-text risk trigger fails at
write time (ADR-0009 sec 4). Borderline predicates that are structurally valid
but suspect MAY emit the same logged LLM-judge event the trigger validator
already uses; this ADR does not require the live judge call (it stays the
deferred Phase 1.5 wiring), only that the escape hatch is logged, not silent.

### Forbidden alternatives

DO NOT, in this ADR or its implementation:

- Lower or remove the Jaccard floor for free-text signals, or drop the ADR-0028
  exact-type equality. The structured form is ADDITIVE; the legacy path stays
  exactly as strict as it is today. Loosening the legacy matcher to "fix" the
  live failure would admit un-grounded confirmations, the confidently-wrong green
  docs/06 names the worst failure mode.
- Make any part of a structured predicate fuzzy. The invariant is matched
  EXACTLY; only declared slots are tolerant, and only on PRESENCE (and optional
  shape), never on a fuzzy comparison of the slot's literal value. A predicate
  with no invariant, or a slot that spans the whole predicate, is rejected, not
  matched-leniently.
- Let the agent self-certify that the predicate holds as a verdict. The agent
  reports the observed fact (filling the slots with what it saw); the body
  evaluates the predicate deterministically and computes the verdict
  (ADR-0009, ADR-0019, ADR-0028). "Predicate holds" is a computed result, not a
  brain assertion.
- Store the bound instance token (the real campaign id, the real hostname) as
  durable knowledge. The slot binding is a per-run observation value, redacted
  by the adapter boundary like any other observation; the seed's predicate
  stores only the ABSTRACT slot, never a concrete instance (the same posture
  ADR-0017 takes on per-session identifiers). A slot name or filler that carries
  a secret / id / PII shape is rejected at the boundary.
- Activate the deferred `states` or `paths` fields (ADR-0009 sec 2, ADR-0011).
  Signals-as-facts lives ENTIRELY inside the existing `Signal` shape (a new
  optional field on the value); it introduces no state graph and no path graph
  and gives neither deferred field a consumer. They stay deferred.
- Re-write existing dogfood seeds silently or in bulk as part of shipping the
  matcher. The free-text path keeps matching them (decision 4); re-seeding into
  the structured form is a separate, explicit, per-signal human action (teach).

## Consequences

Positive:

- A genuinely-passing goal stops reading UNCERTAIN (and so stops being mapped to
  a false REGRESSED) when the agent honestly reports a fact with the real per-run
  instance token instead of the seed's placeholder. The live `create-welcome-popup`
  failure is fixed at its root: the matcher now compares the FACT, not the
  phrasing, and the three missed signals (url, text, network) match by
  construction once re-seeded structurally.
- Matching gets STRICTER on the invariant, not looser. An exact-invariant match
  rejects a wrong status code, a missing row, or a wrong route that 0.5 Jaccard
  could have admitted as a coincidental word-overlap match. The only axis that
  loosens is the declared instance token, which is precisely where Jaccard was
  wrong.
- The change is additive and reuses an existing, proven pattern (the
  `trigger.expect` predicate, the variable slots already in the seeds, the
  `trigger_validator` write-time floor). No new concept is invented; the signal's
  `value` gains the structure the risk's `expect` already has.

Negative:

- A structured seed is more work to author than prose: the human (teach) must
  identify the invariant and name the variable slots. A mis-drawn boundary (an
  instance token left inside the invariant, so it is matched exactly and fails
  next run; or an invariant token pulled into a slot, so the match is too loose)
  is a new authoring error class. Mitigation: the slot syntax is small, the
  validator rejects the worst cases (no invariant, malformed slot), and the
  free-text path remains available for facts that genuinely have no per-run
  instance.
- Two matching paths (structured vs free-text) coexist during migration. This is
  intentional (decision 4) but it is two code paths in `_value_matches` to keep
  correct and tested, until/unless all seeds are migrated.
- The shape vocabulary (decision 5) is deliberately tiny. A SUT whose instance
  token has a shape not covered (`numeric`, `uuid`) falls back to the
  presence-only `{slot}`, which is still strictly better than Jaccard but does
  not catch a malformed-filler regression. Richer shapes are a future ADR if a
  real need appears.

Invariants respected:

- `oracle-sacred` / `no-false-pass` (ADR-0005, docs/06, AGENTS.md non-negotiable
  5): the invariant is matched EXACTLY, stricter than the Jaccard floor; tolerance
  is bounded to declared slots on presence (and optional shape) only; a
  no-invariant predicate is rejected; the agent never self-certifies the verdict.
  No path admits a false PASS.
- `invariants-not-coordinates` (AGENTS.md non-negotiable 1): a signal becomes an
  explicit invariant with named per-run variables, the literal opposite of a
  phrasing-coordinate matched by word-overlap. No selector, xpath, or coordinate
  becomes representable.
- `provenance-and-confidence-mandatory` (ADR-0004): the new value structure rides
  on the existing `Signal`, which keeps mandatory provenance, confidence, and
  status; nothing about the assertion-node contract changes.
- `loud-and-traceable-over-silent-and-convenient` (docs/06): a malformed or
  invariant-less predicate fails loudly at write time; an unconfirmable signal
  stays a loud non-OK (ADR-0028); the instance binding is never silently promoted
  to durable knowledge.
- `no-secrets-tokens-pii-in-knowledge` (ADR-0017, ADR-0026): the seed stores only
  the abstract slot; the bound instance token is a per-run observation redacted at
  the boundary, never written into a knowledge file.

Invariants this ADR does NOT cover:

- The aggregate verdict LABEL for an unconfirmed-but-not-failed goal (a distinct
  "could not confirm signal X" vs a REGRESSED that implies the app broke): owned
  by ADR-0023's taxonomy and any ADR-0028 follow-up. This ADR removes a class of
  false UNCERTAIN at its source (the matcher) but does not relabel the
  UNCERTAIN-to-REGRESSED branch.
- The teach-skill authoring UX for writing a structured predicate (how the human
  is prompted to name slots): an implementation concern for the teach skill
  (ADR-0022), out of scope here; this ADR fixes the model and matcher contract.
- The richer slot shape vocabulary beyond `numeric` / `uuid`: deferred to a
  future ADR if a real SUT need appears.
- Activation of `states` / `paths`: explicitly NOT touched; they stay deferred
  (ADR-0009 sec 2, ADR-0011).

## Relation to prior ADRs

Builds on ADR-0028 (regress agent confirms every believed success signal in its
declared type, Proposed): ADR-0028 aligned the agent contract with the exact-type
matcher but deliberately left the Jaccard floor and `_tokens` unchanged, naming
that as out of scope. This ADR addresses exactly that remaining axis: it replaces
the Jaccard arm with predicate evaluation for structured signals, keeping the
exact-type equality ADR-0028 relies on unchanged. The two are complementary:
ADR-0028 makes the agent report in the RIGHT TYPE; ADR-0030 makes the matcher
compare the RIGHT FACT.

Extends ADR-0009 (Phase-1 R-mode verdict and the `risks.trigger` structured form,
Accepted): generalizes the structured-predicate-with-write-time-validation
pattern from `risks.trigger.expect` to `success/failure` signal values, reusing
the variable-slot convention the existing seeds already use in `expect`. The
verdict rule (failure -> FAIL, all believed success matched -> PASS, otherwise
UNCERTAIN) is unchanged; only WHAT counts as "matched" gains a structured path.

Upholds ADR-0005 (oracle trust by diversity, Accepted) and docs/06 (the false-PASS
asymmetry): the matcher gets stricter on the invariant, never looser; a
confidently-wrong green stays the worst outcome and no new path admits one. The
diversity-of-types requirement for a believed oracle is untouched; a signal's
TYPE and the cross-type diversity rule are independent of whether its value is
prose or a predicate.

Upholds ADR-0017 (auth_state abstract posture, Accepted) and ADR-0026 (session is
a secret, Proposed): a bound instance token (id, hostname, name) is per-run
observation data redacted at the adapter boundary, never durable knowledge; the
seed records only the abstract slot, the same abstract-not-concrete posture
ADR-0017 takes on per-session identifiers.

Builds on ADR-0019 (brain-agnostic body, Accepted) and ADR-0023 (the regress
verdict, Accepted): the predicate is evaluated by the BODY from grounded
observations, never self-certified by the brain; a goal whose structured signal
does not hold stays a loud non-OK routed by the ADR-0023 taxonomy. Does not
supersede any prior ADR.
