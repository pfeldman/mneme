# Praxis testapp - public README (frozen for the regression-recall experiment)

This is the steelman context handed to the `cold_readme` arm of the Phase 1
regression-recall experiment (docs/phase-1-experiment.md). It documents the
public surface of `experiments/ui-mutation/testapp.py` without reference to
the planted-regression manifest (`manifest.json`). Sealed BEFORE any arm
runs; its git sha is recorded in the manifest. Edits invalidate the run.

Authored by an independent reviewer with no knowledge of which regressions
are planted in any release candidate. The reviewer's brief: describe what
this app does for a stranger trying to test it, exactly as an honest
README would describe it for someone joining the project.

---

## What the app is

A tiny, dependency-free HTTP service that simulates a small commerce SaaS.
The point is to have a real app a browser-driving agent can probe end-to-
end: it has stateful flows (auth, cart, checkout), a few permission
boundaries, and pagination, without taking minutes to start up.

Runs on `python http.server`. The default base URL during testing is
`http://127.0.0.1:8000`.

## Capabilities the agent should expect to find

These are the user-level capabilities a returning user assumes the app
provides. Each is something the agent might be asked to confirm or
regression-test.

- **A returning user can authenticate.** A login form at `/login` takes an
  identifier and a password. Posting valid credentials to `/session`
  returns a "Home" page that contains a "Sign out" control, and the
  response sets a session cookie. Invalid credentials return 401 with an
  inline error.
- **A user can search.** `/search` shows a search form. Submitting a
  query returns a results list whose items are rendered as `<li>` inside a
  `<ul class="results">`. An empty query re-shows the form.
- **A user can place an order.** `/cart` shows a "Proceed to checkout"
  button leading to a payment form. Submitting the payment form to
  `/order` with a non-empty card number returns an "Order confirmed" page
  with a confirmation marker.
- **A user can apply a coupon to their cart.** `POST /cart/apply` with
  `coupon=<CODE>` and `subtotal=<INT>` returns JSON describing whether
  the coupon was accepted, the active coupon list, and any reason the
  request was rejected.
- **A user can place an order.** `POST /orders` with a form-encoded card
  number returns JSON containing an `order_id`. The endpoint accepts an
  optional `Idempotency-Key` header.
- **Some pages are reserved.** `GET /settings/admin` is the
  administrative settings page; standard sessions are not expected to
  reach it.
- **List pages take a filter.** `GET /list?page=N&filter=X` returns five
  items per page, and the rendered HTML embeds `data-page`,
  `data-filter`, and `data-effective-filter` attributes describing what
  the server actually applied to the response.

## What the agent should NOT confuse for a regression

Some endpoints are control-plane (used by experiments to set up state).
The agent is NOT expected to probe these as part of testing user
capabilities:

- `/_mutate?set=NAME` and `/_reset` toggle Phase-0 UI mutations.
- `/_break?set=login|search|checkout` and `/_unbreak` toggle the
  oracle-honesty regressions from Phase 0.
- `/_plant?set=NAME`, `/_unplant`, `/_planted` toggle Phase-1 planted
  regressions and read the active manifest. The harness uses these to
  set up each release-candidate state; the agent does not see them
  during normal probing.
- `/_state` returns the current control-plane state.

## Authoring notes

This README intentionally avoids:
- Naming any specific planted regression. The reviewer does not know them.
- Listing slugs in `manifest.json` or any reference to it.
- Documenting exact predicates / thresholds / postconditions for any
  endpoint (no "minimum subtotal", no "same key returns same id"). A
  stranger to this app reads documented capabilities and infers what
  "broken" means; the README hands the cold arm capabilities, not test
  cases.
- A "recommended probing strategy" section. Telling the cold arm WHICH
  edge cases to probe is the manifest in disguise.
- Hinting at which capability is the stale-trap case.
- Praxis-specific framing (`knowledge`, `oracle`, `believed`, `seeded`).
  This is the cold arm's context; Praxis terminology would tip the agent.

## Open authoring caveat (called out for the run record)

The current draft was written by the same Claude session that authored
the planted-regression manifest, NOT by a separately-isolated reviewer
under the 72-hour wash-out protocol the pre-registration calls for
(`pre_registration.md` "Threats to validity"). Before any live arm runs,
this README and `cold_readme_per_goal.md` must be re-authored or
endorsed by an independent reviewer (Pablo role-playing the cold-arm
advocate, with no manifest access in the session). The leakage check
(< 30% Jaccard between any sentence here and any manifest entry's
`expected_observation`) is run as part of the pre-flight checklist in
`LOCAL_RUN.md`.
