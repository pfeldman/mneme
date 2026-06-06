# ADR-0002: The knowledge schema is the neutral interop layer

Status: Accepted

## Context
The runtime ecosystem is split DOM-first vs vision-first and TypeScript vs
Python. A format that ships imperative instructions to all of them will fail.
But a format that describes the *goal and the state* (not the action) can be
produced and consumed by any runtime.

## Decision
`schema/knowledge.schema.json` is a language-neutral description of knowledge,
not a protocol of actions. Transport (when needed) rides on MCP. Each runtime
translates the schema to its own way of acting via an adapter (ADR-0003).

## Consequences
+ Interop without forcing a single execution model.
+ The schema can become a de-facto standard by escaping from a useful tool,
  rather than being designed as a standard up front.
- Two runtimes may regenerate different steps from the same knowledge; that is
  expected and acceptable (the procedure is disposable).
