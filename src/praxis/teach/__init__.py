"""Teach session seams: the LIBRARY half of `/praxis:teach` (ADR-0022).

The teach operation is delivered as a Claude Code skill (`/praxis:teach`),
never a bare CLI command (ADR-0022 decision 1), because it is always
human-in-the-loop: the brain drives a live app and blocks on a human answer.
This package is the non-interactive, browser-free, brain-free machinery the
skill drives, fully testable with no browser and no LLM:

- the typed prompt protocol (credential / navigation-hint / role / confirmation)
  as data (ADR-0022 decision 2);
- navigation hints recorded as the behavioral / network / accessibility / text /
  url invariant they point at, never a raw selector (decision 2);
- the credential-never-persisted contract: only the abstract ADR-0017
  `auth_state` is recorded; an adapter-boundary validator rejects tokens,
  cookies, ids, and PII (decision 5);
- the no-silent-overwrite rule: a re-teach of a believed goal appends a
  contested candidate refinement, never an in-place edit (decision 6);
- the dual end condition (happy-path-observed AND human-confirm) with a budget +
  wall-time backstop that, on non-convergence, writes no goal and emits a loud
  not-converged event (decision 3);
- the human-seeded output: a confirmed session's success oracle carries
  `source_type = human` (the confirming human), the legitimate ADR-0005
  first-oracle seed path (decision 4).

The brain and the browser live in the skill (ADR-0019 / ADR-0003); they never
enter this package, so `import praxis.teach` pulls no runtime and no LLM.
"""
from __future__ import annotations

from .session import (
    INVARIANT_PREFERENCE_ORDER,
    ConfirmationPrompt,
    CredentialLeak,
    CredentialPrompt,
    EndCondition,
    NavigationHintPrompt,
    NavigationInvariant,
    NotConvergedEvent,
    PromptType,
    RolePrompt,
    SelectorLikeReply,
    TeachBudget,
    TeachOutcome,
    TeachPrompt,
    TeachSession,
    assert_no_credential_leak,
    record_navigation_hint,
)

__all__ = [
    "INVARIANT_PREFERENCE_ORDER",
    "ConfirmationPrompt",
    "CredentialLeak",
    "CredentialPrompt",
    "EndCondition",
    "NavigationHintPrompt",
    "NavigationInvariant",
    "NotConvergedEvent",
    "PromptType",
    "RolePrompt",
    "SelectorLikeReply",
    "TeachBudget",
    "TeachOutcome",
    "TeachPrompt",
    "TeachSession",
    "assert_no_credential_leak",
    "record_navigation_hint",
]
