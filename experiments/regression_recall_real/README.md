# Phase 2 regression-recall on a real OSS SUT (Conduit)

This package is the Phase 2 port of the Phase 1 regression-recall
experiment off the synthetic `experiments/ui-mutation/testapp.py` and
onto a real OSS application. The SUT pick (Conduit, the RealWorld
reference Medium-clone) is sealed by ADR-0016; the additive `auth_state`
schema field is sealed by ADR-0017.

Phase 1 sealed artifacts under `experiments/regression_recall/` are NOT
edited by this port; this directory is parallel and independent.

## Layout

```
experiments/regression_recall_real/
  __init__.py              # package docstring + goal slate summary
  README.md                # this file
  pre_registration.md      # sealed-before-run artifact inventory
  manifest.json            # SUT identity + goal slate + planted regressions
  manifest.py              # typed loader for manifest.json
  knowledge/
    login.knowledge.yaml
    publish_article.knowledge.yaml
    favorite_article.knowledge.yaml
    follow_user.knowledge.yaml
    edit_article.knowledge.yaml
  setup/
    docker-compose.yml     # backend + frontend; pinned image tags
    bring_up.sh            # idempotent bring-up; --check / --teardown subcommands
```

## Goal slate (ADR-0016 sec 4)

Five Conduit goals, parallel-but-distinct to the Phase-1 four:

- `login` parallels Phase-1 `login`.
- `publish_article` parallels Phase-1 `checkout` (multi-step mutating flow).
- `favorite_article` parallels Phase-1 `checkout` idempotency.
- `follow_user` parallels Phase-1 `admin_access` (mutating flow with
  authentication precondition + a knowledge-visible self-follow trap).
- `edit_article` parallels Phase-1 `admin_access` directly (auth-scope
  check: only the article author may edit).

Each goal's knowledge file activates the Phase-2 additive `auth_state`
projection (ADR-0017): the agent records that a successful goal leaves
the session authenticated at `user` scope.

## Bring-up

```
bash experiments/regression_recall_real/setup/bring_up.sh
```

ADR-0016 sec 1 caps cold-cache bring-up at 30 minutes wall time on a
developer laptop. The `--check` subcommand verifies an already-running
stack idempotently; `--teardown` removes it.

The slow bring-up test (`tests/test_conduit_bringup.py`) is GATED behind
the env var `PRAXIS_RUN_CONDUIT_BRINGUP=1` so `bash verify.sh` stays fast
by default. To execute the C1 gate explicitly:

```
PRAXIS_RUN_CONDUIT_BRINGUP=1 python -m pytest tests/test_conduit_bringup.py -q
```

## Phase-2 schema delta: `auth_state`

ADR-0017 adds `auth_state: {authenticated: bool, scope: string|null}`
as a projected field on the per-goal knowledge surface. The field is
defined in `schema/knowledge.schema.json` and mirrored in
`src/praxis/model/knowledge.py`; the agreement test in
`tests/test_model_schema_agree.py` catches drift.

The field MUST NOT carry tokens, cookies, user/account/session
identifiers, JWT contents, emails, or tenant/org/workspace scoping
(ADR-0017 sec 2). The validator in the pydantic model rejects these on
write; the adapter-boundary check
(`praxis.adapters.assert_auth_state_observation_safe`) catches
forbidden field names slipping through textual redaction.

## What this package does NOT include yet

Per the Phase-2 plan, the following lands as separate ADRs / commits:

- Multi-writer adversarial harness (ADR-0012).
- Recency-decay projection (ADR-0013).
- E-mode candidate persistence as a sibling event type (ADR-0014).
- Exploration-reward observability (ADR-0015).

Each is its own feature with its own implementation; this package is
the SUT + schema slice (ADR-0016 + ADR-0017) only.
