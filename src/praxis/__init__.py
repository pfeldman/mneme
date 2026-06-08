"""Mneme: a shared semantic-memory layer for QA agents.

Mneme stores *knowledge about a system under test* (goals, state-recognition
signals, success/failure oracles, alternative paths, risks) and keeps it
decoupled from the *procedure* an agent used to reach a goal. Agents read the
projected knowledge to attempt a goal, regenerate their own steps, and write
back observations as immutable events.

Read docs/02-architecture.md before extending this package.

Module map:
    model    -> typed knowledge model (validates against schema/knowledge.schema.json)
    store    -> append-only event log (source of truth) + read API
    merge    -> projection/truth engine: observations -> believed knowledge state
    oracle   -> validation of success/failure signals (the existential guardrail)
    adapters -> per-runtime bridges (Browser Use, Stagehand, Playwright, ...)
"""

__version__ = "0.0.1"
