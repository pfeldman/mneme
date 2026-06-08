# Phase 2 regression-recall pre-registration (Conduit, real-app port)

This file is the manifest of artifacts sealed BEFORE any arm of the
Phase-2 regression-recall experiment runs on Conduit. Editing any of
them post-run invalidates the run and requires a new pre-registration ADR.

This pre-registration is the Phase-2 mirror of
`experiments/regression_recall/pre_registration.md`. It reuses the
Phase-1 mitigations (frozen READMEs, sealed manifest, prompts pinned by
sha) and adds two Phase-2 specific items: the SUT identity (Conduit,
ADR-0016) and the additive schema field (`auth_state`, ADR-0017) the
goal slate consumes.

Pre-registered to defend against:

- p-hacking the kill criteria after seeing results.
- mid-experiment prompt tweaks that overfit to early arm performance.
- moving the cold_readme baseline to make memory's edge bigger.
- under porting pressure, capturing tokens / cookies / user_ids / session_ids
  / PII as durable knowledge because they happen to be available in the
  trace at write time (the canonical porting-leak shape ADR-0017 forbids).

## Sealed artifacts (commit BEFORE the first arm runs)

| Artifact | Path | What it fixes |
|----------|------|----------------|
| Ground-truth manifest | `manifest.json` | Which regressions are planted; the `expected_observation` per regression that the false-positive judge grades against. |
| Goal slate | `manifest.json` `goals` block | Five Conduit goals (login, publish_article, favorite_article, follow_user, edit_article) authored parallel-but-distinct to the Phase-1 four (ADR-0016 sec 4). |
| Seeded knowledge | `knowledge/*.knowledge.yaml` | One knowledge file per goal. Each carries the Phase-1 success/failure signals + risks + uncertainties AND the Phase-2 additive `auth_state` projection (ADR-0017). |
| SUT setup | `setup/docker-compose.yml`, `setup/bring_up.sh` | The Conduit bring-up Pablo or CI invokes; the C1 ceiling check (`tests/test_conduit_bringup.py`, gated) runs against this. |
| Schema delta | `schema/knowledge.schema.json` + `src/praxis/model/` | The additive `auth_state` field lands here, not in this experiment's seeds, so the schema/model/agreement-test triple stays the boundary (ADR-0017 sec 4). |

## Conduit identity (recorded once)

- Source: `https://github.com/gothinkster/realworld` (RealWorld project,
  MIT license).
- Reference backend: `gothinkster/realworld-node-express` (Node/Express).
- Reference frontend: `gothinkster/realworld-react-redux` (React).
- Local bring-up: `bash experiments/regression_recall_real/setup/bring_up.sh`.
- API base URL: `http://localhost:3000/api`.
- Frontend URL: `http://localhost:4100`.
- C1 bring-up ceiling: 30 minutes wall clock on a developer laptop (ADR-0016).

## Recording at run time

Mirroring the Phase-1 run manifest, before the first arm fires the harness
records:

```json
{
  "run_id": "...",
  "started_at": "...",
  "release": "phase-2-r1-conduit",
  "conduit_backend_image_digest": "...",
  "conduit_frontend_image_digest": "...",
  "manifest_sha": "...",
  "knowledge_dir_sha": "...",
  "prompts_py_sha": "...",
  "judge_prompt_sha": "...",
  "metrics_sha": "...",
  "praxis_git_sha": "...",
  "model": "...",
  "model_provider": "...",
  "budget_tokens_per_goal": ...,
  "n_seeds": ...
}
```

## auth_state-specific defences

ADR-0017 sec 2 lists forbidden value shapes inside `auth_state`. The
pre-registration explicitly records that:

- No knowledge YAML in this experiment carries a token, cookie, user_id,
  session_id, JWT, email, tenant_id, org_id, or workspace_id under any
  field. The validator in `src/praxis/model/knowledge.py` enforces this on
  load; the adapter-boundary validator in `src/praxis/adapters/spi.py`
  enforces it on write.
- Observations that an arm produces during a run pass through `redact()`
  (see `src/praxis/adapters/spi.py`); for observations that feed
  `auth_state` specifically, the boundary-level
  `assert_auth_state_observation_safe` raises `AuthStateLeakError` if a
  forbidden field name slipped through redaction.
- The redaction discipline is tested in `tests/test_adapters.py` (the
  `auth_state` block).

## Out of scope for Phase 2 (deferred)

The following are deferred per ADR-0011 and are NOT part of this
pre-registration. Adding them mid-run is forbidden.

- Stagehand head-to-head adapter arm (Phase 1.5).
- Cross-SUT sweep beyond Conduit (Phase 3).
- Auditor scenarios as R-mode input (rejected; offline oracle stress).
- `refuted` status (Phase 1.5).
- Tenant scoping (ADR-0012 path convention; not a knowledge surface).
