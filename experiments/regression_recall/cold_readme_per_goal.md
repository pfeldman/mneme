# cold_readme per-goal sentences (frozen)

One sentence per goal, handed to the `cold_readme` arm alongside the goal
string + the frozen README. Authored by the same independent reviewer who
wrote `README_FROZEN.md`, with no access to the planted-regression
manifest. Sealed before any arm runs.

The sentence answers a returning user's mental "what should I check"
without naming any specific bug or saying anything Praxis-specific.

## login
A returning user signs in with the form at `/login`; success looks like
the Home page rendering with a "Sign out" control and a session cookie
set; if anything else happens (server error, redirect to a forbidden page,
cookie not set, or a banner that does not match invalid-credentials wording),
that is a regression worth flagging.

## search
A signed-in user submits a query on `/search` and expects a results list
with at least one item; an empty results list for a non-empty query, the
filter being silently dropped on subsequent pages, or a 5xx are all
regressions.

## checkout
A signed-in user proceeds from `/cart`, submits a card on the payment
form, and expects a confirmation page; double-orders on retry, charges
without confirmation, and decline-pages for valid cards are all
regressions. Coupon application is part of this flow: a coupon with a
minimum subtotal must reject below the threshold; two coupons must not
stack in the same cart.
