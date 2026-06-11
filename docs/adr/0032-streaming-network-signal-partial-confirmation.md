# ADR-0032: Whether an observed-but-still-streaming network request counts as a typed partial confirmation

Status: Proposed (2026-06-11)

## Context

ADR-0028 fixed a false REGRESSED that came from the agent expressing a believed
success signal in a different evidence type than the seed declared: it aligned
the regress prompt with the exact-type matcher and pushed type discipline to
teach time, WITHOUT loosening `_value_matches`. A second, distinct trap was hit
in real-app dogfooding that ADR-0028's teach guidance did not cover, and it is
worse because the fact was genuinely observed once.

A goal seeded a believed `network` success signal: "submitting the question
triggers a POST to the chat turn endpoint that returns a 2xx response". The
endpoint STREAMS its response (Server-Sent Events). Headless, the POST fires and
then sits OPEN with no final status while it trickles tokens; when the regress
agent samples the network log there is no 2xx to read back, because the request
has not finalized. So the agent honestly leaves the `network` signal unconfirmed
(ADR-0028 forbids fabricating a 2xx it never saw). Fewer than all believed
success signals match, the run is UNCERTAIN, and `classify_goal`'s fall-through
maps that to a FALSE REGRESSED (ADR-0023). The interactive surface confirmed the
same underlying feature fine; the streamed answer DID render. The fact is true,
it WAS observed at the network level once at teach time, but it is not reliably
RE-observable there on a later sampled run.

The teach-time half of this is owned by a separate, already-shipped change (the
praxis-teach signal-type guidance now warns against typing a fact `network` when
the endpoint streams and tells the author to type the VISIBLE EFFECT
behaviorally / by text instead, while keeping the ADR-0005 two-distinct-types
diversity). That guidance prevents NEW seeds from falling into the trap. This ADR
asks the separate question for EXISTING seeds and for the runtime path: should
the regress envelope be allowed to treat an observed-but-still-streaming request
as a TYPED PARTIAL confirmation of its declared-type signal, instead of as an
absence that routes to a false REGRESSED?

## The proposal under consideration

Two shapes were examined; neither ships in this ADR, because each is a
verdict-semantics change that touches the ADR-0023 taxonomy and risks a false
PASS. They are recorded here for the maintainer to decide after a live proof.

### Option A (rejected as written): the matcher counts an observed-streaming request as matching the 2xx seed.

Make `_value_matches` (or `verdict_from_observations`) accept a `network`
observation that says "the POST to the chat turn endpoint was sent and is
streaming, no final status observed" as a MATCH for the seed "... returns a 2xx
response". This is a FALSE PASS by construction and is rejected: the seed asserts
a 2xx was returned; the agent never saw the 2xx. Counting "the request is open"
as confirming "returns a 2xx" confirms a fact that was not observed, the exact
confidently-wrong green ADR-0028, ADR-0005, and docs/06 forbid. It also reopens
the matcher-loosening ADR-0028 closed. NOT pursued.

### Option B (the real question): a new typed PARTIAL outcome distinct from both OK and a missing signal.

The agent emits an HONEST, declared-type observation that the request was
OBSERVED-AND-STREAMING (a real grounded network observation, distinct from
fabricating a 2xx), and the runner treats a believed `network` signal whose only
live observation is "observed-and-streaming" as a PARTIAL confirmation: NOT a
clean OK (the final status was never read), but NOT the absence that today routes
to REGRESSED. Mechanically this needs all of:

- a new piece of brain-emitted execution provenance on `RunResult` (analogous to
  `healthy_equivalent_observed`), set when the agent saw the named request open
  and stream but never finalize within the sampling window;
- a new verdict member between PASS and UNCERTAIN (a PARTIAL / STREAMING state)
  or a new `AggregateVerdict`, plus a routing rule in `classify_goal`;
- a decision on whether that outcome `fails_run` (a red CI gate) or is a distinct
  loud-but-not-failing line like STALE.

`_value_matches`, the exact-type equality, and the Jaccard floor stay UNCHANGED
under Option B (it does not loosen the matcher; it adds a parallel typed-evidence
channel). But it DOES change the per-run verdict taxonomy.

## Why this is proposed-only, not implemented

The guardrail on this work is explicit: do not implement a change that touches
the ADR-0023 taxonomy or risks a false PASS; lean conservative; the project would
rather have a false UNCERTAIN than risk a false green. Option B trips both:

- It is a verdict-semantics decision that adds a new outcome to the OK /
  REGRESSED / STALE / AUTH-EXPIRED taxonomy ADR-0023 (and ADR-0026) own. New
  members of that taxonomy have each been their own maintainer-accepted ADR after
  a live finding (AUTH-EXPIRED is the precedent), not a quiet runner edit.

- It risks masking a real regression. A chat endpoint that is genuinely BROKEN -
  it streams forever and never returns, hangs, or errors mid-stream - presents to
  the sampler exactly as "observed-and-streaming, no final status". If
  "observed-and-streaming" is allowed to count as a partial confirmation that
  keeps the goal out of REGRESSED, a hung-stream regression is excused as
  benign. That is a false negative on a regression, a sibling of the false PASS,
  and docs/06 names a silently-excused break as the worst outcome.

The honest middle ground (the agent records an observed-and-streaming
observation as real evidence in the declared type) is fine as a RECORD, but on
its own it changes no verdict: an observation that does not match the seed by
`_value_matches` leaves the believed signal unmatched and the run stays
UNCERTAIN. To make it move the verdict you must add the PARTIAL routing, which is
exactly the taxonomy decision above. So there is no clean, conservative,
self-evidently-safe implementation; the safe move is to ship the teach-time
guidance (done, separate) and leave the runtime PARTIAL question to the
maintainer.

## Decision the maintainer must make

Decide, after a live proof, whether to introduce a typed PARTIAL/STREAMING
outcome (Option B) and, if so:

1. Is an observed-but-still-streaming believed `network` signal a distinct
   verdict (PARTIAL / STREAMING) rather than the absence that routes to
   REGRESSED today?
2. Does that outcome FAIL the run (red gate) or is it a loud-but-non-failing
   line like STALE that routes to "re-seed the signal as the visible effect"?
3. How is a GENUINELY hung / broken stream (the false-negative-of-a-regression
   case) distinguished from a healthy stream that simply has not finalized in the
   sampling window, so the new outcome cannot excuse a real regression? Without a
   crisp distinguisher, Option B must not ship.

The recommended default until that decision is made: rely on the teach-time
guidance to keep new seeds off `network` for streaming endpoints, and re-seed any
EXISTING streaming `network` signal as its visible-effect equivalent
(`behavioral` / `text` / `url`) via a teach re-seed (ADR-0022), which fixes the
live goal with no taxonomy change and no false-PASS surface.

## Consequences

Positive (of staying proposed-only):

- The matcher, the exact-type equality, the Jaccard floor, and the verdict
  taxonomy are all untouched, so this ADR cannot introduce a false PASS or a
  false-negative-of-a-regression. The live streaming goal is fixable today by a
  teach re-seed onto the visible effect.

Negative (of staying proposed-only):

- An existing seed that still types a streaming fact as `network` keeps coming
  back as a false REGRESSED until it is re-seeded. The cost is pushed to a human
  re-seed rather than absorbed by the runner; that is the conservative trade the
  guardrail asks for (a false UNCERTAIN / a manual re-seed over a false green).

Invariants respected:

- `oracle-sacred` / `no-false-pass` (ADR-0005, docs/06, AGENTS.md non-negotiable
  5): nothing here loosens the matcher or counts an unobserved 2xx as confirmed;
  the unconfirmed signal stays a loud non-OK (ADR-0028).
- `invariants-not-coordinates`: no selectors or coordinates introduced; the
  discussion stays on the durability-ordered signal types.
- `loud-and-traceable-over-silent-and-convenient`: the conservative default keeps
  the streaming-vs-broken ambiguity LOUD (a non-OK that a human re-seeds) rather
  than silently excusing it as benign.

## Relation to prior ADRs

Extends ADR-0028 (regress confirms every believed success signal in its declared
type, Accepted): ADR-0028's teach corollary (decision 4) constrains seed types to
ones a regress agent can reproduce; the streaming case is a subtype where a
network fact was reproducible ONCE at teach time but is not reliably
re-observable on a sampled run. The teach-time fix extends decision 4; this ADR
records the runtime PARTIAL question decision 4 does not answer.

Refines ADR-0023 (the OK / REGRESSED / STALE break-vs-drift report, Accepted):
the streaming false REGRESSED is the same UNCERTAIN -> REGRESSED fall-through
ADR-0028 named. Whether to add a PARTIAL / STREAMING member to that taxonomy is
the open decision this ADR hands the maintainer; it does not change the taxonomy.

Upholds ADR-0005 (oracle trust by diversity, Accepted) and docs/06 (the
false-PASS asymmetry): the proposal deliberately does NOT ship any change that
could count a non-finalized stream as a confirmation, because a confidently-wrong
green and a silently-excused hung stream are both worse than a false UNCERTAIN.
Does not supersede any prior ADR.
