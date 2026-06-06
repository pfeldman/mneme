# 01 — Vision and thesis

## Thesis
The valuable, durable artifact in testing is not the procedure — it is the
**knowledge of what the system should do and how to tell whether it did.**
If agents can build and maintain that knowledge themselves, you get tests that
survive UI change and improve over time, instead of artifacts that rot on every
redesign.

## Reframing: agent-maintained model-based testing
Model-based testing (MBT) has existed for ~25 years: model the app as states,
transitions, and oracles; derive tests from the model. It rarely won in practice
because **maintaining the model by hand cost more than maintaining the tests.**

The single change that makes this idea live now: **agents author and maintain the
model.** The cost curve that killed MBT inverts. Everything in this project is
downstream of that one bet — and so is its main risk (the model can be wrong, and
agent-authored wrongness is silent; see docs/06).

## What we store vs. throw away
- **Persist (the asset):** goal, semantic state identity (redundant recognition
  signals), success/failure oracles as observable assertions, alternative paths
  as a graph of intents, risks with triggers, open uncertainties, and provenance
  + confidence on all of it.
- **Discard (disposable):** the click-by-click procedure, selectors, coordinates,
  timings, run-specific data. Regenerated fresh each run from the knowledge.

## What "good" looks like
1. A new agent, handed only the knowledge for a goal, achieves the goal without
   replaying anyone's steps.
2. After a UI redesign that breaks a recorded script, the knowledge-driven agent
   still succeeds, and updates the memory with what changed.
3. Multiple agents converge on a shared, increasingly accurate model — without
   poisoning it or collapsing onto a single happy path.

## Where it sits in the ecosystem
Not a competitor to Browser Use / Stagehand / Skyvern / Playwright — a **layer on
top of them.** They execute; Mneme remembers. The knowledge schema is the neutral
interchange format (ADR-0002); MCP is the transport; adapters bridge each runtime.

## Anti-goals
- Not a runner, recorder, or selector engine.
- Not a single-runtime tool.
- Not a place to store procedures, secrets, or run data.
- Not a standards-body effort on day one — earn the format through a useful tool.
