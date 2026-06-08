"""Phase 2 regression-recall on a real OSS SUT (Conduit / RealWorld).

ADR-0016 picks Conduit (Medium-clone reference, MIT-licensed) as the Phase 2
system under test, replacing the synthetic `experiments/ui-mutation/testapp.py`
that Phase 1 ran against. This package mirrors the Phase 1 harness shape
(manifest, knowledge YAMLs, pre-registration) but with fresh content for
Conduit goals; no Phase 1 sealed artifact is edited.

Goal slate (ADR-0016 sec 4, parallel-but-distinct from Phase 1's four):

  * login                 - authenticate a returning user (parallel to Phase 1 login).
  * publish_article       - create + publish an article (parallel to checkout).
  * favorite_article      - toggle the favorite on someone else's article (idempotency).
  * follow_user           - follow another user (parallel to admin_access mutating flow).
  * edit_article          - owner-only edit of an article (auth scope parallel to admin).

The Phase 2 schema delta (`auth_state`) is defined in ADR-0017 and lands at the
model/schema layer; this package consumes it but does not own the schema
change. The auth_state field is REQUIRED for goals here that probe a
logged-in surface, because the same authentication precondition is shared
across multiple goals (Phase 1 testapp could re-infer it from per-signal
evidence; on Conduit the cross-goal reuse is the point).

Bring-up: see `setup/bring_up.sh` for the docker-compose path. The script
exits zero in well under 30 minutes on a developer laptop (ADR-0016 sec 1
C1). The live test that exercises bring-up is gated behind
`PRAXIS_RUN_CONDUIT_BRINGUP=1` so the default `bash verify.sh` stays fast.
"""
