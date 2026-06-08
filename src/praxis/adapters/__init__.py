"""Runtime adapters (the interop boundary).

The core is runtime-agnostic. An adapter does exactly two things (ADR-0003):
  read  -> hydrate an agent with the believed knowledge for a goal
  write -> translate what the agent observed back into store events
The knowledge schema is the neutral interchange format (ADR-0002); adapters are
the ONLY runtime-specific code, and they redact secrets/PII at this boundary
(docs/06). Adapters are optional install extras - importing this package does
not require any runtime to be installed (heavy runtime imports are lazy).

Public API:
    KnowledgeAdapter                       -- the tiny, stable SPI (Protocol)
    redact                                 -- strip secrets/PII before anything enters the store
    redact_observation                     -- redact-in-place an ObservedSignal
    assert_auth_state_observation_safe     -- loud boundary check for auth_state writes (ADR-0017)
    AuthStateLeakError                     -- raised when an auth_state observation carries a credential
    BrowserUseAdapter                      -- the Browser Use bridge (Phase-0 runtime)
"""
from __future__ import annotations

from .browser_use import BrowserUseAdapter, CandidateRejected
from .spi import (
    AuthStateLeakError,
    KnowledgeAdapter,
    assert_auth_state_observation_safe,
    redact,
    redact_observation,
)

__all__ = [
    "AuthStateLeakError",
    "BrowserUseAdapter",
    "CandidateRejected",
    "KnowledgeAdapter",
    "assert_auth_state_observation_safe",
    "redact",
    "redact_observation",
]
