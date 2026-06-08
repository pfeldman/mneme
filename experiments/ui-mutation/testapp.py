"""A tiny, dependency-free test app (System Under Test) for the LOCAL live run.

Runs on Python's stdlib `http.server` only — no Flask, no new dependencies. Serves
the three flows (login / search / checkout) as real HTML pages a browser agent can
drive, and exposes endpoints to inject the four UI mutations at runtime so the
recorded-script baseline breaks while a knowledge-driven agent recovers.

Run it:
    python experiments/ui-mutation/testapp.py --port 8000

Mutation control (the harness or you toggle these between runs):
    GET /_mutate?set=rename_control      # also: move_field, swap_email_for_username,
                                         #       insert_intermediate_step
    GET /_reset                          # back to baseline
    GET /_state                          # JSON: active mutations + broken flows

Regression control (for the live oracle-honesty check - the goal becomes
UNREACHABLE; success signals never appear):
    GET /_break?set=login                # also: search, checkout
    GET /_unbreak                        # heal all flows

Phase-1 planted-regression control (docs/phase-1-experiment.md). A planted
regression is a real bug a knowledge-equipped agent should DETECT (not the
coarse "make unreachable" of /_break). The harness toggles these per release
candidate, runs each arm, then queries /_planted to grade recall:
    GET /_plant?set=k1_save10_at_49      # also: t1_login_500, k2_stack_codes,
                                         #   k3_double_order, k4_admin_settings,
                                         #   k5_filter_lost, s1_oracle_lies
    GET /_unplant                        # heal all planted regressions
    GET /_planted                        # ground-truth manifest (post-run)

Phase-1 endpoints (probed by the planted regressions):
    POST /cart/apply   body: coupon=...&subtotal=...   (coupon predicate + stacking)
    POST /orders       header: Idempotency-Key=...      (idempotent order creation)
    GET  /settings/admin                                (admin-only)
    GET  /list?page=N&filter=X                          (paginated; filter must persist)

Ground-truth success signals per flow (what an oracle should confirm):
    login    -> a "Sign out" control is present AND POST /session returned 2xx + cookie
    search   -> a non-empty results list is shown AND GET /search returned 2xx
    checkout -> an order-confirmation number is shown AND POST /order returned 2xx
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Active mutations (process-global; toggled via /_mutate, /_reset).
MUTATIONS: set[str] = set()
VALID = {"rename_control", "move_field", "swap_email_for_username", "insert_intermediate_step"}

# Deliberately-regressed flows (the goal becomes UNREACHABLE; success signals never
# appear). Toggled via /_break, /_unbreak. Used for the live oracle-honesty check:
# the agent must report failure and the oracle must NOT false-pass.
BROKEN: set[str] = set()
VALID_BREAK = {"login", "search", "checkout"}

# Phase-1 planted regressions (docs/phase-1-experiment.md). Toggled via
# /_plant?set=NAME, cleared by /_unplant. These differ from BROKEN: a planted
# regression is a real bug a knowledge-equipped agent should DETECT, not just
# a coarse "make the goal unreachable" toggle. /_planted returns the manifest
# (which regressions are active right now) so the experiment harness can
# compute recall vs ground truth post-run.
PLANTED: set[str] = set()
VALID_PLANT = {
    "t1_login_500",          # tourist: POST /session always 500s
    "t2_search_blank",       # tourist: /search returns a blank results page
    "k1_save10_at_49",       # coupons: SAVE10 accepted with subtotal < 50
    "k2_stack_codes",        # coupons: two coupons stack in same cart
    "k3_double_order",       # idempotency: same Idempotency-Key creates two orders
    "k4_admin_settings",     # permissions: non-admin reaches admin settings
    "k5_filter_lost",        # paginated: filter dropped on page >= 2
    "s1_oracle_lies",        # stale-trap: success signal fires but session invalid
}

# Phase-1 stateful endpoints (cart/coupons/orders/list). Process-global so
# /_reset returns to a clean baseline before each release.
_CART_COUPONS: list[str] = []                # coupons applied to "the cart"
_CART_SUBTOTAL: int = 49                     # cents-free integer for simplicity
_ORDER_KEY_TO_ID: dict[str, str] = {}        # Idempotency-Key -> order_id
_NEXT_ORDER_SEQ: int = 1000
_AUTH_SESSIONS: set[str] = set()             # valid session cookies (for s1_oracle_lies)
_COUPONS = {"SAVE10": 10, "BIGSAVE": 25}     # discount amount, abstract units

_PAGE = """<!doctype html><html><head><title>{title}</title></head>
<body><h1>{title}</h1>{body}
<hr><nav><a href="/login">login</a> | <a href="/search">search</a>
| <a href="/cart">checkout</a></nav></body></html>"""


def page(title: str, body: str) -> bytes:
    return _PAGE.format(title=title, body=body).encode("utf-8")


# --------------------------------------------------------------------------- login

def login_form() -> str:
    id_label = "Username" if "swap_email_for_username" in MUTATIONS else "Email"
    id_field = (f'<label>{id_label}<input name="identifier" type="text"></label>')
    secret_field = '<label>Password<input name="secret" type="password"></label>'
    fields = [id_field, secret_field]
    if "move_field" in MUTATIONS:
        fields.reverse()  # positions swap; a positional recorded selector breaks
    submit_label = "Log in" if "rename_control" in MUTATIONS else "Sign in"
    return (
        '<form method="post" action="/session">'
        + "".join(f"<p>{f}</p>" for f in fields)
        + f'<button type="submit">{submit_label}</button></form>'
    )


# --------------------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    server_version = "MnemeTestApp/0"

    def log_message(self, *args: object) -> None:  # quiet by default
        pass

    # -- helpers --
    def _send(self, body: bytes, status: int = 200, ctype: str = "text/html",
              cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, status: int = 200) -> None:
        self._send(json.dumps(obj).encode(), status, "application/json")

    # -- routing --
    def do_GET(self) -> None:  # noqa: N802 (stdlib API name)
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)

        if path in ("/", "/index.html"):
            self._send(page("Acme test app",
                            "<p>Flows: login, search, checkout.</p>"))
        elif path == "/login":
            self._send(page("Sign in", login_form()))
        elif path == "/search":
            self._search(q)
        elif path == "/cart":
            label = "Begin checkout" if "rename_control" in MUTATIONS else "Proceed to checkout"
            self._send(page("Cart",
                            f'<form method="post" action="/cart/checkout">'
                            f'<button type="submit">{label}</button></form>'))
        elif path == "/_mutate":
            self._mutate(q)
        elif path == "/_reset":
            MUTATIONS.clear()
            self._json({"active": []})
        elif path == "/_break":
            self._break(q)
        elif path == "/_unbreak":
            BROKEN.clear()
            self._json({"broken": []})
        elif path == "/_state":
            self._json({
                "active": sorted(MUTATIONS),
                "broken": sorted(BROKEN),
                "planted": sorted(PLANTED),
            })
        elif path == "/_plant":
            self._plant(q)
        elif path == "/_unplant":
            _clear_planted_state()
            self._json({"planted": []})
        elif path == "/_planted":
            # Ground-truth manifest endpoint: harness compares observations
            # against this AFTER each arm runs to compute recall.
            self._json({"planted": sorted(PLANTED)})
        elif path == "/_reset_state":
            # Clears side-effect state (cart, orders, sessions) WITHOUT
            # touching PLANTED slugs. Call between arms in the experiment
            # harness so two arms running back-to-back against the same
            # plant state do not contaminate each other through process-
            # global side effects (a real blocker found in the Phase-1
            # dry run: cart accumulating coupons across arms).
            _CART_COUPONS.clear()
            _ORDER_KEY_TO_ID.clear()
            _AUTH_SESSIONS.clear()
            self._json({"reset": True, "planted_still_active": sorted(PLANTED)})
        elif path == "/me":
            self._me()
        elif path == "/list":
            self._list(q)
        elif path == "/settings/admin":
            self._admin_settings()
        else:
            self._send(page("Not found", "<p>404</p>"), status=404)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode()) if length else {}

        if url.path == "/session":
            self._session(form)
        elif url.path == "/cart/checkout":
            self._checkout_step()
        elif url.path == "/checkout/continue":
            # The interstitial's "Continue" -> proceed to payment.
            self._send(page("Payment", _payment_form()))
        elif url.path == "/order":
            self._order(form)
        elif url.path == "/cart/apply":
            self._cart_apply(form)
        elif url.path == "/orders":
            self._orders(form)
        else:
            self._send(page("Not found", "<p>404</p>"), status=404)

    # -- flow handlers --
    def _session(self, form: dict) -> None:
        ident = (form.get("identifier") or [""])[0]
        secret = (form.get("secret") or [""])[0]
        # t1_login_500: planted tourist-visible regression - POST /session 500s.
        if "t1_login_500" in PLANTED:
            self._send(page("Server error", "<p>500</p>"), status=500)
            return
        # MOVE_FIELD swaps inputs; a positional recorded script types the password
        # into the identifier box and vice-versa, so credentials are wrong -> fail.
        ok = (bool(ident) and bool(secret) and "@" not in secret
              and ident != "secret-typed-as-id" and "login" not in BROKEN)
        if ok:
            # s1_oracle_lies: planted stale-trap. The behavioral + network success
            # signals BOTH fire (Sign out shown, 2xx + cookie set), but the session
            # cookie is NOT recorded as valid - any follow-up authenticated request
            # will fail. An R-mode arm trusting believed signals blindly will PASS
            # this regression. Catching it requires checking session validity (an
            # accumulated risk from prior releases).
            valid_cookie = "session=ok; Path=/"
            if "s1_oracle_lies" not in PLANTED:
                _AUTH_SESSIONS.add("ok")
            self._send(page("Home", '<p>Welcome.</p><a href="/logout">Sign out</a>'),
                       cookie=valid_cookie)
        else:
            self._send(page("Sign in",
                            '<p class="error">Invalid credentials.</p>' + login_form()),
                       status=401)

    def _search(self, q: dict) -> None:
        query = (q.get("q") or [""])[0]
        if not query:
            label = "Find" if "rename_control" in MUTATIONS else "Search"
            self._send(page("Search",
                            f'<form method="get" action="/search">'
                            f'<label>Search<input name="q" type="text"></label>'
                            f'<button type="submit">{label}</button></form>'))
        elif "search" in BROKEN or "t2_search_blank" in PLANTED:
            # Regressed: the query returns nothing - no results list ever
            # appears. Both the Phase-0 BROKEN toggle and the Phase-1 planted
            # slug route here so the manifest can use a uniform
            # /_plant?set=t2_search_blank instead of a special-cased /_break.
            self._send(page("Results", '<p class="error">No results found.</p>'))
        else:
            self._send(page("Results",
                            f'<ul class="results"><li>Result for {query} #1</li>'
                            f'<li>Result for {query} #2</li></ul>'))

    def _checkout_step(self) -> None:
        if "insert_intermediate_step" in MUTATIONS:
            self._send(page("One more step",
                            '<form method="post" action="/checkout/continue">'
                            '<button type="submit">Continue</button></form>'))
        else:
            self._send(page("Payment", _payment_form()))

    def _order(self, form: dict) -> None:
        card = (form.get("card") or [""])[0]
        if card and "checkout" not in BROKEN:
            self._send(page("Order confirmed",
                            '<p class="confirmation">Order #A1024 confirmed.</p>'))
        else:
            self._send(page("Payment",
                            '<p class="error">Payment declined.</p>' + _payment_form()),
                       status=402)

    def _mutate(self, q: dict) -> None:
        name = (q.get("set") or [""])[0]
        if name not in VALID:
            self._json({"error": f"unknown mutation {name!r}", "valid": sorted(VALID)}, 400)
            return
        MUTATIONS.add(name)
        self._json({"active": sorted(MUTATIONS)})

    def _break(self, q: dict) -> None:
        name = (q.get("set") or [""])[0]
        if name not in VALID_BREAK:
            self._json({"error": f"unknown flow {name!r}", "valid": sorted(VALID_BREAK)}, 400)
            return
        BROKEN.add(name)
        self._json({"broken": sorted(BROKEN)})

    def _plant(self, q: dict) -> None:
        name = (q.get("set") or [""])[0]
        if name not in VALID_PLANT:
            self._json({"error": f"unknown plant {name!r}",
                        "valid": sorted(VALID_PLANT)}, 400)
            return
        PLANTED.add(name)
        self._json({"planted": sorted(PLANTED)})

    # -- Phase-1 endpoints (coupons / orders / list / admin) --

    def _cart_apply(self, form: dict) -> None:
        """POST /cart/apply  body: coupon=SAVE10
        Discount is applied only if subtotal meets the coupon's minimum.
        Planted regressions:
          k1_save10_at_49 -> accepts SAVE10 below the threshold ($49).
          k2_stack_codes  -> allows a second coupon when one is already applied.
        """
        global _CART_SUBTOTAL  # noqa: PLW0603 - intentional process-global
        coupon = (form.get("coupon") or [""])[0]
        subtotal_raw = (form.get("subtotal") or [str(_CART_SUBTOTAL)])[0]
        try:
            subtotal = int(subtotal_raw)
        except ValueError:
            self._json({"error": "bad subtotal"}, 400)
            return
        _CART_SUBTOTAL = subtotal
        if coupon not in _COUPONS:
            self._json({"error": "unknown coupon", "applied": False}, 404)
            return
        # k2: stacking is normally rejected (one coupon per cart).
        if _CART_COUPONS and "k2_stack_codes" not in PLANTED:
            self._json({"error": "only one coupon per cart",
                        "applied": False, "active": _CART_COUPONS}, 409)
            return
        # k1: minimum subtotal predicate (SAVE10 requires subtotal >= 50).
        min_required = 50 if coupon == "SAVE10" else 100
        below = subtotal < min_required
        if below and "k1_save10_at_49" not in PLANTED:
            self._json({"error": f"subtotal {subtotal} below minimum {min_required}",
                        "applied": False}, 422)
            return
        _CART_COUPONS.append(coupon)
        self._json({
            "applied": True, "coupon": coupon,
            "discount": _COUPONS[coupon], "subtotal": subtotal,
            "active": list(_CART_COUPONS),
        })

    def _orders(self, form: dict) -> None:
        """POST /orders  header: Idempotency-Key
        Same Idempotency-Key must return the same order_id (the regression).
        Planted k3_double_order -> a fresh order_id is minted every time.
        """
        global _NEXT_ORDER_SEQ  # noqa: PLW0603
        key = self.headers.get("Idempotency-Key", "")
        card = (form.get("card") or [""])[0]
        if not card:
            self._json({"error": "missing card"}, 422)
            return
        if not key:
            # No idempotency key supplied: each request gets a fresh order.
            order_id = f"A{_NEXT_ORDER_SEQ}"
            _NEXT_ORDER_SEQ += 1
            self._json({"order_id": order_id, "idempotent": False})
            return
        if "k3_double_order" not in PLANTED and key in _ORDER_KEY_TO_ID:
            self._json({"order_id": _ORDER_KEY_TO_ID[key], "idempotent": True})
            return
        order_id = f"A{_NEXT_ORDER_SEQ}"
        _NEXT_ORDER_SEQ += 1
        _ORDER_KEY_TO_ID[key] = order_id
        self._json({"order_id": order_id,
                    "idempotent": "k3_double_order" not in PLANTED})

    def _me(self) -> None:
        """GET /me - authenticated identity endpoint.
        Returns 200 with the session id ONLY if the cookie matches an entry
        in _AUTH_SESSIONS (which `_session` populates on successful login).
        Returns 401 otherwise.

        This endpoint is what catches `s1_oracle_lies`: with that slug
        planted, `_session` does NOT register the session, so `/me` returns
        401 even though /session looked fine. An R-mode arm that trusts only
        the believed login success_signals will pass; an arm that includes a
        follow-up authenticated check will catch it.
        """
        cookie = self.headers.get("Cookie", "")
        sid = ""
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("session="):
                sid = kv.split("=", 1)[1].strip()
        if sid and sid in _AUTH_SESSIONS:
            self._json({"authenticated": True, "session_id": sid})
        else:
            self._json({"authenticated": False, "reason":
                         "session cookie missing or invalid"}, 401)

    def _admin_settings(self) -> None:
        """GET /settings/admin  - admin-only page.
        Planted k4_admin_settings -> serves admin content without auth check.
        """
        cookie = self.headers.get("Cookie", "")
        is_admin = "role=admin" in cookie
        if is_admin or "k4_admin_settings" in PLANTED:
            self._send(page("Admin settings",
                            '<p class="admin">Internal config. (admin only)</p>'))
        else:
            self._send(page("Forbidden",
                            '<p class="error">403 - admin role required.</p>'),
                       status=403)

    def _list(self, q: dict) -> None:
        """GET /list?page=N&filter=X  - paginated list with optional filter.
        Planted k5_filter_lost -> filter is silently dropped for page >= 2.
        """
        try:
            page_no = int((q.get("page") or ["1"])[0])
        except ValueError:
            page_no = 1
        flt = (q.get("filter") or [""])[0]
        effective_filter = flt
        if page_no >= 2 and "k5_filter_lost" in PLANTED:
            effective_filter = ""
        items = [f"item-{i}" for i in range((page_no - 1) * 5, page_no * 5)]
        if effective_filter:
            items = [i for i in items if effective_filter in i]
        body = (
            f'<p data-page="{page_no}" data-filter="{flt}" '
            f'data-effective-filter="{effective_filter}">'
            f"page {page_no}, filter={flt!r}, effective_filter={effective_filter!r}"
            f"</p>"
            f'<ul class="list">{"".join(f"<li>{i}</li>" for i in items)}</ul>'
        )
        self._send(page("List", body))


def _payment_form() -> str:
    label = "Complete purchase" if "rename_control" in MUTATIONS else "Place order"
    return (f'<form method="post" action="/order">'
            f'<label>Card number<input name="card" type="text"></label>'
            f'<button type="submit">{label}</button></form>')


def _clear_planted_state() -> None:
    """Reset all Phase-1 planted-regression toggles + the stateful endpoints'
    in-memory state. Called by /_unplant; the experiment harness calls this
    between release-candidate runs to start clean.
    """
    PLANTED.clear()
    _CART_COUPONS.clear()
    _ORDER_KEY_TO_ID.clear()
    _AUTH_SESSIONS.clear()


def main() -> None:
    ap = argparse.ArgumentParser(description="Mneme local test app (SUT)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Praxis test app on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print("  flows: /login /search /cart   mutate: /_mutate?set=NAME /_reset")
    print("  break: /_break?set=login|search|checkout /_unbreak   state: /_state")
    print("  plant: /_plant?set=NAME /_unplant   manifest: /_planted")
    print("  phase-1 endpoints: /cart/apply /orders /settings/admin /list?page=N")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
