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

Regression control (for the live oracle-honesty check — the goal becomes
UNREACHABLE; success signals never appear):
    GET /_break?set=login                # also: search, checkout
    GET /_unbreak                        # heal all flows

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
            self._json({"active": sorted(MUTATIONS), "broken": sorted(BROKEN)})
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
        else:
            self._send(page("Not found", "<p>404</p>"), status=404)

    # -- flow handlers --
    def _session(self, form: dict) -> None:
        ident = (form.get("identifier") or [""])[0]
        secret = (form.get("secret") or [""])[0]
        # MOVE_FIELD swaps inputs; a positional recorded script types the password
        # into the identifier box and vice-versa, so credentials are wrong → fail.
        ok = (bool(ident) and bool(secret) and "@" not in secret
              and ident != "secret-typed-as-id" and "login" not in BROKEN)
        if ok:
            # behavioral (Sign out present) + network (2xx + cookie) success signals.
            self._send(page("Home", '<p>Welcome.</p><a href="/logout">Sign out</a>'),
                       cookie="session=ok; Path=/")
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
        elif "search" in BROKEN:
            # Regressed: the query returns nothing — no results list ever appears.
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


def _payment_form() -> str:
    label = "Complete purchase" if "rename_control" in MUTATIONS else "Place order"
    return (f'<form method="post" action="/order">'
            f'<label>Card number<input name="card" type="text"></label>'
            f'<button type="submit">{label}</button></form>')


def main() -> None:
    ap = argparse.ArgumentParser(description="Mneme local test app (SUT)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mneme test app on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print("  flows: /login /search /cart   mutate: /_mutate?set=NAME /_reset")
    print("  break: /_break?set=login|search|checkout /_unbreak   state: /_state")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
