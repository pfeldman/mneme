# cold_readme per-goal sentences (frozen)

One sentence per goal, handed to the `cold_readme` arm alongside the goal
string + the frozen README. Authored by the same independent reviewer who
wrote `README_FROZEN.md`, with no access to the planted-regression
manifest. Sealed before any arm runs.

The sentence answers a returning user's mental "what should I check"
without naming any specific bug or saying anything Praxis-specific.

## login
A returning user signs in via `/login` and expects to land on the home
page with a "Sign out" control available. Report anything that differs
from that documented happy outcome.

## search
A signed-in user submits a query on `/search` and expects a results
list to render. Report anything that differs from that.

## checkout
A signed-in user proceeds from `/cart` through the payment form and
expects a confirmation page. Report anything that differs from that, in
the checkout flow or in the coupon / order endpoints the README
describes.
