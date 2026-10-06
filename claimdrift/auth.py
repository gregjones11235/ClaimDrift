"""Accounts and sessions (decision 2026-10-03: no anonymous access; every page needs a login).

Session-based, stored in Elasticsearch:
  auth_users     doc id = normalised email; {email, name, role, password_hash, created_at}
  auth_sessions  doc id = sha256(session token); {email, role, created_at, expires_at}

The browser only ever holds the raw token in an HttpOnly cookie (COOKIE_NAME) on the frontend's domain; the index holds
its hash, so reading the index does not yield usable sessions. Sessions are read by document id (real-time in ES, no
refresh lag) and expire after SESSION_TTL; logout deletes the document, so a session can be revoked at once.

Roles: "customer" (self-registration) and "admin" (created only with `python -m claimdrift.auth create-user`).
Passwords: scrypt from the standard library (no extra dependency).

Store: Elasticsearch by default; an in-memory store for the BFF's seed mode (BFF_SEED_DATA=1) and for tests.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

COOKIE_NAME = "cd_session"
SESSION_TTL = dt.timedelta(hours=12)
ROLES = ("customer", "admin")
USERS_INDEX, SESSIONS_INDEX = "auth_users", "auth_sessions"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
# no minimum length beyond "not empty" (user decision 2026-10-03); the maximum only bounds the hashing cost
MIN_PASSWORD, MAX_PASSWORD = 1, 128

# scrypt cost: ~50 ms per hash on one core; 16 MiB memory
_N, _R, _P = 2**14, 8, 1


class AuthError(Exception):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status, self.error, self.message = status, error, message


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(h)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# A hash of a random password: login for an unknown email still pays one scrypt, so response time does not reveal
# which emails have accounts.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- stores
class MemoryStore:
    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get_user(self, email: str) -> dict | None:
        return self.users.get(email)

    def create_user(self, email: str, doc: dict) -> bool:
        with self._lock:
            if email in self.users:
                return False
            self.users[email] = doc
            return True

    def put_user(self, email: str, doc: dict) -> None:
        self.users[email] = doc

    def get_session(self, sid: str) -> dict | None:
        return self.sessions.get(sid)

    def put_session(self, sid: str, doc: dict) -> None:
        self.sessions[sid] = doc

    def delete_session(self, sid: str) -> None:
        self.sessions.pop(sid, None)

    def delete_expired(self, now_iso: str) -> None:
        for k in [k for k, v in self.sessions.items() if v["expires_at"] < now_iso]:
            self.sessions.pop(k, None)


USERS_MAPPING = {"mappings": {"dynamic": "strict", "properties": {
    "email": {"type": "keyword"}, "name": {"type": "keyword"}, "role": {"type": "keyword"},
    "password_hash": {"type": "keyword", "index": False}, "created_at": {"type": "date"}}}}
SESSIONS_MAPPING = {"mappings": {"dynamic": "strict", "properties": {
    "email": {"type": "keyword"}, "role": {"type": "keyword"},
    "created_at": {"type": "date"}, "expires_at": {"type": "date"}}}}


class ESStore:
    """claimdrift.es (cloud by default, local docker with CLAIMDRIFT_LOCAL=1). Creates its two indices on first use."""

    def __init__(self) -> None:
        self._ready = False
        self._lock = threading.Lock()

    def _es(self):
        from . import es
        if not self._ready:
            with self._lock:
                if not self._ready:
                    for index, body in ((USERS_INDEX, USERS_MAPPING), (SESSIONS_INDEX, SESSIONS_MAPPING)):
                        try:
                            es.request("PUT", index, body)
                        except es.ESError as e:
                            if "resource_already_exists_exception" not in e.body:
                                raise
                    self._ready = True
        return es

    def get_user(self, email: str) -> dict | None:
        return self._es().source(USERS_INDEX, email)

    def create_user(self, email: str, doc: dict) -> bool:
        es = self._es()
        try:
            es.put(USERS_INDEX, email, doc, op_type="create")
            return True
        except es.ESError as e:
            if e.status == 409:
                return False
            raise

    def put_user(self, email: str, doc: dict) -> None:
        self._es().put(USERS_INDEX, email, doc)

    def get_session(self, sid: str) -> dict | None:
        return self._es().source(SESSIONS_INDEX, sid)

    def put_session(self, sid: str, doc: dict) -> None:
        self._es().put(SESSIONS_INDEX, sid, doc)

    def delete_session(self, sid: str) -> None:
        es = self._es()
        try:
            es.request("DELETE", es.doc_path(SESSIONS_INDEX, sid) + "?refresh=wait_for")
        except es.ESError as e:
            if e.status != 404:
                raise

    def delete_expired(self, now_iso: str) -> None:
        self._es().delete_by_query(SESSIONS_INDEX, {"range": {"expires_at": {"lt": now_iso}}})


# ---------------------------------------------------------------- service
class Auth:
    # failed logins per email: at most MAX_FAILURES within FAILURE_WINDOW seconds (per process)
    MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60
    CLEANUP_EVERY = 3600

    def __init__(self, store) -> None:
        self.store = store
        self._failures: dict[str, list[float]] = {}
        self._last_cleanup = 0.0
        self._lock = threading.Lock()

    # -- accounts
    def create_user(self, email: str, password: str, role: str = "customer", name: str = "") -> dict:
        email = normalise_email(email)
        if not EMAIL_RE.match(email):
            raise AuthError(400, "invalid_email", "enter a valid email address")
        if not MIN_PASSWORD <= len(password or "") <= MAX_PASSWORD:
            raise AuthError(400, "invalid_password", f"enter a password of at most {MAX_PASSWORD} characters")
        if role not in ROLES:
            raise AuthError(400, "invalid_role", f"role must be one of {ROLES}")
        doc = {"email": email, "name": (name or "").strip()[:100], "role": role,
               "password_hash": hash_password(password), "created_at": _now().isoformat()}
        if not self.store.create_user(email, doc):
            raise AuthError(409, "email_taken", "an account with this email already exists")
        return public_user(doc)

    def register(self, email: str, password: str, name: str = "") -> dict:
        """Self-registration always creates a customer; admins are created from the command line only."""
        return self.create_user(email, password, "customer", name)

    # -- sessions
    def login(self, email: str, password: str) -> tuple[str, dict]:
        email = normalise_email(email)
        self._check_throttle(email)
        user = self.store.get_user(email) if email else None
        ok = verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH)
        if not (user and ok):
            self._record_failure(email)
            raise AuthError(401, "invalid_credentials", "wrong email or password")
        self._failures.pop(email, None)
        self._maybe_cleanup()
        token = secrets.token_urlsafe(32)
        now = _now()
        self.store.put_session(_token_id(token), {"email": email, "role": user["role"], "created_at": now.isoformat(),
                                                  "expires_at": (now + SESSION_TTL).isoformat()})
        return token, public_user(user)

    def session_user(self, token: str | None) -> dict | None:
        """The logged-in user for a session token, or None (no token, unknown, expired)."""
        if not token:
            return None
        s = self.store.get_session(_token_id(token))
        if not s or s["expires_at"] < _now().isoformat():
            return None
        return {"email": s["email"], "role": s["role"]}

    def logout(self, token: str | None) -> None:
        if token:
            self.store.delete_session(_token_id(token))

    # -- helpers
    def _check_throttle(self, email: str) -> None:
        cutoff = time.time() - self.FAILURE_WINDOW
        with self._lock:
            recent = [t for t in self._failures.get(email, []) if t > cutoff]
            self._failures[email] = recent
        if len(recent) >= self.MAX_FAILURES:
            raise AuthError(429, "too_many_attempts", "too many failed logins; try again in 15 minutes")

    def _record_failure(self, email: str) -> None:
        with self._lock:
            self._failures.setdefault(email, []).append(time.time())

    def _maybe_cleanup(self) -> None:
        """Expired sessions are already ignored on read; this only keeps the index small (at most hourly)."""
        if time.time() - self._last_cleanup < self.CLEANUP_EVERY:
            return
        self._last_cleanup = time.time()
        try:
            self.store.delete_expired(_now().isoformat())
        except Exception as exc:  # noqa: BLE001 - housekeeping must never break a login
            print(f"auth: expired-session cleanup failed: {exc}")


def public_user(doc: dict) -> dict:
    return {"email": doc["email"], "name": doc.get("name") or "", "role": doc["role"]}


def token_from_cookie_header(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            return v
    return None


def cookie_secure() -> bool:
    """Secure cookies on Cloud Run (PORT is set there), plain locally over http://localhost. AUTH_COOKIE_SECURE=0/1
    overrides."""
    v = os.getenv("AUTH_COOKIE_SECURE")
    return v == "1" if v in ("0", "1") else bool(os.getenv("PORT"))


def set_cookie_header(token: str) -> str:
    return (f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={int(SESSION_TTL.total_seconds())}"
            + ("; Secure" if cookie_secure() else ""))


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" + ("; Secure" if cookie_secure() else "")


_default: Auth | None = None


def default() -> Auth:
    global _default
    if _default is None:
        _default = Auth(ESStore())
    return _default


def _cli() -> None:
    import argparse
    import getpass
    ap = argparse.ArgumentParser(prog="python -m claimdrift.auth", description="Manage ClaimDrift accounts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="create an account (the only way to create an admin)")
    c.add_argument("email")
    c.add_argument("--role", choices=ROLES, default="customer")
    c.add_argument("--name", default="")
    c.add_argument("--password-env", help="read the password from this environment variable instead of prompting")
    r = sub.add_parser("set-password", help="reset an account's password")
    r.add_argument("email")
    r.add_argument("--password-env")
    a = ap.parse_args()
    pw = os.environ.get(a.password_env, "") if a.password_env else getpass.getpass("password: ")
    auth = default()
    if a.cmd == "create-user":
        try:
            print(auth.create_user(a.email, pw, a.role, a.name))
        except AuthError as e:
            raise SystemExit(f"{e.error}: {e.message}")
    else:
        email = normalise_email(a.email)
        user = auth.store.get_user(email)
        if not user:
            raise SystemExit(f"no account {email}")
        if not MIN_PASSWORD <= len(pw) <= MAX_PASSWORD:
            raise SystemExit(f"enter a password of at most {MAX_PASSWORD} characters")
        auth.store.put_user(email, user | {"password_hash": hash_password(pw)})
        print(f"password reset for {email}")


if __name__ == "__main__":
    _cli()
