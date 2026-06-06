# ADR-0003: Runtime-specific code lives only behind an adapter SPI

Status: Accepted

## Context
Core value (the knowledge model, store, projection, oracle) must not depend on
any single browser runtime, or it becomes "yet another framework" locked to one
tool.

## Decision
Define a small adapter SPI with two responsibilities: `read_knowledge(goal_id)`
to hydrate an agent, and `write_observations(...)` to emit store events.
Adapters are optional install extras. Core has zero runtime dependencies.

## Consequences
+ New runtimes (Browser Use, Stagehand, Playwright, future) are additive.
+ Core stays testable without a browser.
- The SPI is a real contract and must stay minimal and stable.
