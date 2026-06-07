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
  `coupon=<CODE>` and `subtotal=<INT>` applies a discount if the coupon
  exists, the subtotal meets the coupon's minimum, and no other coupon is
  already active in the cart. Successful applications return JSON with
  `applied: true` and the active coupon list. Known coupon codes: `SAVE10`
  (minimum subtotal 50), `BIGSAVE` (minimum subtotal 100).
- **A user can place an idempotent order.** `POST /orders` with a form-
  encoded card number returns JSON containing an `order_id`. When the
  request carries an `Idempotency-Key` header, repeating the request with
  the SAME key returns the SAME `order_id` (the response's `idempotent`
  field flips to `true` on repeats).
- **Only an admin can reach admin settings.** `GET /settings/admin`
  returns 403 unless the session presents an `admin` role cookie; with
  `Cookie: role=admin` it returns the admin settings page.
- **List pages preserve filters.** `GET /list?page=N&filter=X` returns 5
  items per page with the filter applied. The filter applies to every
  page, not only the first; the rendered HTML embeds `data-page`,
  `data-filter`, and `data-effective-filter` so an agent can confirm what
  the server actually applied.

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

## Recommended probing strategy (cold reader's guide)

A reasonable cold approach for a returning user:

1. Walk the three Phase-0 happy paths (login, search, checkout) end to
   end and confirm each capability above behaves as described.
2. For the cart endpoints, send a representative coupon, send the same
   coupon at a low subtotal, send a second coupon while the first is
   active. Check whether the responses match the rules above.
3. For orders, send the same `Idempotency-Key` twice and verify the
   server returns the same `order_id`.
4. Hit `/settings/admin` without an admin cookie and confirm it 403s.
5. Apply a filter on `/list?page=1` and follow to `/list?page=2` with the
   same filter; verify the filter is still applied (look at
   `data-effective-filter`).

If any of those probes returns something different from the rules above,
flag it as a regression.

## Authoring notes

This README intentionally avoids:
- Naming any specific planted regression. The reviewer doesn't know them.
- Listing the slugs in `manifest.json` or any reference to it.
- Hinting at which capability is the stale-trap case (the manifest hides
  that on purpose; this README does too).
- Praxis-specific framing (`knowledge`, `oracle`, `believed`, `seeded`).
  This is the cold arm's context; Praxis terminology would tip the agent.
