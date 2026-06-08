"""Phase-1 testapp endpoint smoke tests.

Drive the new endpoints (coupons / orders / admin / list) directly via the
stdlib request handler, confirming that each planted regression flips the
expected behavior. Runs the server in-process on an ephemeral port so the
tests don't need a separate process. Phase-0 mutations + breaks are exercised
elsewhere; this file is strictly the new Phase-1 surface.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

# Importing the testapp under its experiment path:
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "ui-mutation"))

import testapp  # noqa: E402


@contextmanager
def _server():
    # Reset all process-global state between tests.
    testapp.MUTATIONS.clear()
    testapp.BROKEN.clear()
    testapp._clear_planted_state()
    # Bind to port 0 so the OS picks a free one - no flakiness from collisions.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), testapp.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str) -> tuple[int, str, dict]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - local server
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers or {})


def _post(url: str, data: dict[str, str], headers: dict[str, str] | None = None
          ) -> tuple[int, str, dict]:
    body = "&".join(f"{k}={v}" for k, v in data.items()).encode()
    req = urllib.request.Request(url, method="POST", data=body,
                                  headers={"Content-Type": "application/x-www-form-urlencoded",
                                            **(headers or {})})
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers or {})


def _json(body: str) -> dict:
    return json.loads(body)


# --- /_plant + /_planted ---------------------------------------------------


def test_plant_and_planted_manifest() -> None:
    with _server() as base:
        s, b, _ = _get(f"{base}/_planted")
        assert s == 200 and _json(b)["planted"] == []
        s, b, _ = _get(f"{base}/_plant?set=k1_save10_at_49")
        assert s == 200 and _json(b)["planted"] == ["k1_save10_at_49"]
        s, b, _ = _get(f"{base}/_planted")
        assert _json(b)["planted"] == ["k1_save10_at_49"]


def test_unknown_plant_rejected() -> None:
    with _server() as base:
        s, b, _ = _get(f"{base}/_plant?set=nope")
        assert s == 400


# --- k1_save10_at_49 -------------------------------------------------------


def test_coupon_save10_rejected_below_threshold_by_default() -> None:
    with _server() as base:
        s, b, _ = _post(f"{base}/cart/apply", {"coupon": "SAVE10", "subtotal": "49"})
        assert s == 422
        assert _json(b)["applied"] is False


def test_coupon_save10_accepted_below_threshold_when_planted() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=k1_save10_at_49")
        s, b, _ = _post(f"{base}/cart/apply", {"coupon": "SAVE10", "subtotal": "49"})
        assert s == 200
        assert _json(b)["applied"] is True


# --- k2_stack_codes --------------------------------------------------------


def test_second_coupon_rejected_by_default() -> None:
    with _server() as base:
        _post(f"{base}/cart/apply", {"coupon": "SAVE10", "subtotal": "100"})
        s, b, _ = _post(f"{base}/cart/apply", {"coupon": "BIGSAVE", "subtotal": "100"})
        assert s == 409


def test_second_coupon_accepted_when_stacking_planted() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=k2_stack_codes")
        _post(f"{base}/cart/apply", {"coupon": "SAVE10", "subtotal": "100"})
        s, b, _ = _post(f"{base}/cart/apply", {"coupon": "BIGSAVE", "subtotal": "100"})
        assert s == 200
        assert _json(b)["applied"] is True


# --- k3_double_order -------------------------------------------------------


def test_idempotency_key_dedupes_orders_by_default() -> None:
    with _server() as base:
        s1, b1, _ = _post(f"{base}/orders", {"card": "4111"},
                          headers={"Idempotency-Key": "abc"})
        s2, b2, _ = _post(f"{base}/orders", {"card": "4111"},
                          headers={"Idempotency-Key": "abc"})
        assert s1 == 200 and s2 == 200
        assert _json(b1)["order_id"] == _json(b2)["order_id"]
        assert _json(b2)["idempotent"] is True


def test_idempotency_key_creates_two_orders_when_planted() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=k3_double_order")
        _, b1, _ = _post(f"{base}/orders", {"card": "4111"},
                         headers={"Idempotency-Key": "abc"})
        _, b2, _ = _post(f"{base}/orders", {"card": "4111"},
                         headers={"Idempotency-Key": "abc"})
        assert _json(b1)["order_id"] != _json(b2)["order_id"]


# --- k4_admin_settings -----------------------------------------------------


def test_admin_settings_forbidden_by_default() -> None:
    with _server() as base:
        s, _, _ = _get(f"{base}/settings/admin")
        assert s == 403


def test_admin_settings_reachable_when_planted() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=k4_admin_settings")
        s, b, _ = _get(f"{base}/settings/admin")
        assert s == 200
        assert "admin only" in b.lower()


# --- k5_filter_lost --------------------------------------------------------


def test_filter_persists_across_pages_by_default() -> None:
    with _server() as base:
        s, b, _ = _get(f"{base}/list?page=2&filter=item")
        assert s == 200
        assert 'data-effective-filter="item"' in b


def test_filter_lost_on_page_2_when_planted() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=k5_filter_lost")
        s, b1, _ = _get(f"{base}/list?page=2&filter=item")
        assert 'data-effective-filter=""' in b1
        # Page 1 with the same plant active still keeps the filter:
        _, b2, _ = _get(f"{base}/list?page=1&filter=item")
        assert 'data-effective-filter="item"' in b2


# --- t1_login_500 ----------------------------------------------------------


def test_login_500_when_planted() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=t1_login_500")
        s, _, _ = _post(f"{base}/session", {"identifier": "x", "secret": "y"})
        assert s == 500


# --- t2_search_blank -------------------------------------------------------


def test_search_returns_results_by_default() -> None:
    with _server() as base:
        s, b, _ = _get(f"{base}/search?q=foo")
        assert s == 200
        assert "results" in b.lower()
        assert "no results found" not in b.lower()


def test_search_blank_when_planted() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=t2_search_blank")
        s, b, _ = _get(f"{base}/search?q=foo")
        assert s == 200
        assert "no results found" in b.lower()


# --- /me + s1 stale-trap detection ----------------------------------------


def test_me_returns_200_for_valid_session() -> None:
    """Baseline: a successful login produces a session that /me accepts."""
    with _server() as base:
        # Capture the Set-Cookie value from a successful login.
        s, _, headers = _post(f"{base}/session",
                               {"identifier": "alice", "secret": "pw"})
        assert s == 200
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        # Pass it back to /me.
        req = urllib.request.Request(f"{base}/me", headers={"Cookie": cookie})
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            body = json.loads(resp.read().decode())
            assert body["authenticated"] is True


def test_me_returns_401_when_s1_oracle_lies_planted() -> None:
    """s1_oracle_lies: /session looks fine, but the session is not valid;
    /me rejects with 401. This is the stale-trap an R-mode arm trusting
    only the believed login signals would miss without probing /me."""
    with _server() as base:
        _get(f"{base}/_plant?set=s1_oracle_lies")
        s, _, headers = _post(f"{base}/session",
                               {"identifier": "alice", "secret": "pw"})
        assert s == 200  # login looks OK at HTTP level
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        # But /me with that cookie still returns 401.
        req = urllib.request.Request(f"{base}/me", headers={"Cookie": cookie})
        try:
            urllib.request.urlopen(req)  # noqa: S310
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401


# --- /_reset_state -------------------------------------------------------


def test_reset_state_clears_cart_but_keeps_plants() -> None:
    """Real blocker found in the Phase-1 dry run: cart accumulated across
    arms. /_reset_state clears side effects without touching PLANTED."""
    with _server() as base:
        _get(f"{base}/_plant?set=k1_save10_at_49")
        # Apply a coupon to dirty the cart.
        _post(f"{base}/cart/apply", {"coupon": "SAVE10", "subtotal": "100"})
        # Hit reset.
        s, b, _ = _get(f"{base}/_reset_state")
        assert s == 200
        body = _json(b)
        assert body["reset"] is True
        assert "k1_save10_at_49" in body["planted_still_active"]
        # The cart is fresh: a second apply now succeeds without "stacking".
        s2, _, _ = _post(f"{base}/cart/apply",
                          {"coupon": "BIGSAVE", "subtotal": "100"})
        assert s2 == 200  # would have been 409 if SAVE10 still in the cart


# --- /_unplant resets all -------------------------------------------------


def test_unplant_clears_everything() -> None:
    with _server() as base:
        _get(f"{base}/_plant?set=k1_save10_at_49")
        _get(f"{base}/_plant?set=k3_double_order")
        s, b, _ = _get(f"{base}/_unplant")
        assert s == 200 and _json(b)["planted"] == []
        # Coupon predicate is back in force.
        s, _, _ = _post(f"{base}/cart/apply", {"coupon": "SAVE10", "subtotal": "49"})
        assert s == 422
