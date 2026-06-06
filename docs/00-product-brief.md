# 00 — Product brief

## One line
A shared semantic-memory layer that lets QA agents store and maintain knowledge
about an application — goals, recognition signals, success/failure oracles,
alternative paths, risks — decoupled from the steps any run used.

## The problem
Today's testing artifacts (recorded scripts, even AI self-healing caches) store
**procedures**. Procedures break on every UI change. AI tools patch the procedure
(re-resolve a selector); they don't accumulate durable understanding of the app.
The thing teams pay for — confidence that the app works — is not what gets stored.

## The insight
Separate the **model of the target** (what to look for, what success means, what
can go wrong) from the **procedure** (how I clicked this time). Persist the model;
throw the procedure away. Let agents maintain the model as they run.

## Why now
This is model-based testing, which historically failed because humans couldn't
afford to maintain the model. Capable agents change that cost structure. The
adjacent market (Stagehand, Skyvern, Browser Use) has converged on self-healing
*procedural* caches — nobody is persisting the *semantic* model. That is the gap.

## Who it's for
Teams running agentic QA who already feel the pain of re-deriving the same app
understanding on every run and every agent, and who want tests that survive
redesigns instead of breaking on them.

## The risk that defines the product
The oracle problem: knowledge authored by fallible agents becomes authoritative.
A confidently-wrong success signal makes shared memory worse than no memory —
tests pass while the app is broken. Brittleness is loud; bad knowledge is silent.
The entire product is really a **trust-management layer** over agent-authored
knowledge. That is also where the defensible value lives.

## Shape of the business (see docs/07)
Open-source library + schema to seed adoption → hosted **trust layer** (provenance,
conflict resolution, poisoning detection, governance) as the product → de-facto
standard as a consequence, never as the opening move.

## The one experiment that decides everything
`experiments/ui-mutation/`: does knowledge-driven step regeneration survive a UI
change that breaks a recorded script, more cheaply than a cold agent? If not,
there is no moat. Build that before anything else.
