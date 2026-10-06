"""Accounts, sessions and the BFF's access rules (claimdrift.auth; decision 2026-10-03: no anonymous access).

Offline: an in-memory store replaces Elasticsearch, and the BFF handler runs on a local port.
Run: uv run python apps/bff/tests/test_auth.py
"""
import datetime as dt
import http.client
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claimdrift import auth  # noqa: E402
from apps.bff import server  # noqa: E402


def make_auth() -> auth.Auth:
    a = auth.Auth(auth.MemoryStore())
    a.create_user("cust@example.org", "customer-pass", "customer")
    a.create_user("admin@example.org", "admin-pass-1", "admin")
    return a


class TestPasswords(unittest.TestCase):
    def test_hash_roundtrip_and_salt(self) -> None:
        h1, h2 = auth.hash_password("s3cret-pass"), auth.hash_password("s3cret-pass")
        self.assertNotEqual(h1, h2)  # random salt
        self.assertTrue(auth.verify_password("s3cret-pass", h1))
        self.assertFalse(auth.verify_password("wrong-pass", h1))
        self.assertFalse(auth.verify_password("x", "garbage"))


class TestAccountsAndSessions(unittest.TestCase):
    def setUp(self) -> None:
        self.a = make_auth()

    def test_register_creates_customer_only(self) -> None:
        u = self.a.register("New@Example.org ", "longenough", "Ann")
        self.assertEqual(u, {"email": "new@example.org", "name": "Ann", "role": "customer"})
        self.assertEqual(self.a.register("tiny@example.org", "12345")["role"], "customer")  # no minimum length
        self.assertNotIn("password_hash", u)

    def test_register_validation(self) -> None:
        for email, pw, code in (("not-an-email", "longenough", "invalid_email"),
                                ("a@b.org", "", "invalid_password"),
                                ("cust@example.org", "longenough", "email_taken")):
            with self.assertRaises(auth.AuthError) as cm:
                self.a.register(email, pw)
            self.assertEqual(cm.exception.error, code)

    def test_login_session_logout(self) -> None:
        token, user = self.a.login("CUST@example.org", "customer-pass")
        self.assertEqual(user["role"], "customer")
        self.assertEqual(self.a.session_user(token), {"email": "cust@example.org", "role": "customer"})
        # the store holds the token's hash, never the token
        self.assertNotIn(token, self.a.store.sessions)
        self.a.logout(token)
        self.assertIsNone(self.a.session_user(token))

    def test_wrong_password_and_unknown_user_look_the_same(self) -> None:
        for email in ("cust@example.org", "nobody@example.org"):
            with self.assertRaises(auth.AuthError) as cm:
                self.a.login(email, "wrong-pass")
            self.assertEqual((cm.exception.status, cm.exception.error), (401, "invalid_credentials"))

    def test_expired_session_is_rejected(self) -> None:
        token, _ = self.a.login("cust@example.org", "customer-pass")
        sid = auth._token_id(token)
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
        self.a.store.sessions[sid]["expires_at"] = past
        self.assertIsNone(self.a.session_user(token))

    def test_throttle_after_repeated_failures(self) -> None:
        for _ in range(auth.Auth.MAX_FAILURES):
            with self.assertRaises(auth.AuthError):
                self.a.login("cust@example.org", "wrong-pass")
        with self.assertRaises(auth.AuthError) as cm:
            self.a.login("cust@example.org", "customer-pass")  # even the right password waits
        self.assertEqual(cm.exception.status, 429)

    def test_cookie_helpers(self) -> None:
        self.assertEqual(auth.token_from_cookie_header("a=1; cd_session=abc; b=2"), "abc")
        self.assertIsNone(auth.token_from_cookie_header("a=1"))
        self.assertIn("HttpOnly", auth.set_cookie_header("abc"))
        self.assertIn("Max-Age=0", auth.clear_cookie_header())


class TestBffAccessRules(unittest.TestCase):
    """The BFF over HTTP: 401 without a session, 403 for a customer on operator routes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._saved = server.AUTH, server.DATA_SOURCE
        server.AUTH = make_auth()
        server.DATA_SOURCE = server.SeedDataSource()  # offline: /api/stats reads the bundled seed
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        server.AUTH, server.DATA_SOURCE = cls._saved

    def call(self, method: str, path: str, body=None, cookie: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        return r.status, data, r.getheader("Set-Cookie")

    def login(self, email: str, pw: str) -> str:
        status, data, set_cookie = self.call("POST", "/api/auth/login", {"email": email, "password": pw})
        self.assertEqual(status, 200, data)
        return set_cookie.split(";")[0]

    def test_health_is_public(self) -> None:
        self.assertEqual(self.call("GET", "/api/health")[0], 200)

    def test_data_routes_need_a_session(self) -> None:
        for path in ("/api/drift-events", "/api/auth/me", "/api/review-queue", "/api/events/stream"):
            self.assertEqual(self.call("GET", path)[0], 401, path)
        self.assertEqual(self.call("POST", "/api/selfcheck/references", {"text": "x"})[0], 401)

    def test_stats_are_public_but_pending_count_is_admin_only(self) -> None:
        status, anon, _ = self.call("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertIn("drift_events_total", anon)
        self.assertNotIn("review_pending_total", anon)
        cust = self.call("GET", "/api/stats", cookie=self.login("cust@example.org", "customer-pass"))[1]
        self.assertNotIn("review_pending_total", cust)
        admin = self.call("GET", "/api/stats", cookie=self.login("admin@example.org", "admin-pass-1"))[1]
        self.assertIn("review_pending_total", admin)

    def test_customer_cannot_reach_operator_routes(self) -> None:
        c = self.login("cust@example.org", "customer-pass")
        self.assertEqual(self.call("GET", "/api/auth/me", cookie=c)[1]["user"]["role"], "customer")
        self.assertEqual(self.call("GET", "/api/review-queue", cookie=c)[0], 403)
        self.assertEqual(self.call("GET", "/api/review/events/x", cookie=c)[0], 403)
        self.assertEqual(self.call("POST", "/api/review/events/x", {"decision": "rejected"}, cookie=c)[0], 403)

    def test_register_logs_in_and_logout_revokes(self) -> None:
        status, data, set_cookie = self.call("POST", "/api/auth/register",
                                             {"email": "fresh@example.org", "password": "longenough"})
        self.assertEqual((status, data["user"]["role"]), (200, "customer"))
        c = set_cookie.split(";")[0]
        self.assertEqual(self.call("GET", "/api/auth/me", cookie=c)[0], 200)
        status, _, cleared = self.call("POST", "/api/auth/logout", {}, cookie=c)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", cleared)
        self.assertEqual(self.call("GET", "/api/auth/me", cookie=c)[0], 401)

    def test_bad_login(self) -> None:
        status, data, set_cookie = self.call("POST", "/api/auth/login", {"email": "cust@example.org", "password": "nope-nope"})
        self.assertEqual((status, data["error"], set_cookie), (401, "invalid_credentials", None))


if __name__ == "__main__":
    unittest.main()
