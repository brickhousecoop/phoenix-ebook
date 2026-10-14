"""Who may use the website: users, passwords and sign-in cookies (ADR 0003).

Users are an email address and an argon2id password hash, kept as a JSON object
in ``PHOENIX_USERS`` or, failing that, in ``~/.phoenix_users.json`` (maintained by
``scripts/add_user.py``). With neither, nobody can sign in. The website reads
users only through ``load_users()``, so moving them to a store later changes
only that function.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from phoenix_ebook.models import SourceSpec

USERS_ENV = "PHOENIX_USERS"
SECRET_ENV = "PHOENIX_SESSION_SECRET"
USERS_FILE = Path.home() / ".phoenix_users.json"
COOKIE = "phoenix_session"
MAX_AGE = 30 * 24 * 60 * 60  # seconds a sign-in lasts

log = logging.getLogger(__name__)
_hasher = PasswordHasher()
_local_secret = secrets.token_bytes(32)  # without PHOENIX_SESSION_SECRET: sign-ins last until the app restarts
_dummy_hash: str | None = None


def normalize(email: str) -> str:
    """The form an email address is stored and compared in: 'Ana@X.org ' -> 'ana@x.org'."""
    return email.strip().lower()


def load_users() -> dict[str, str] | None:
    """Email -> argon2id hash, from ``PHOENIX_USERS`` or else ``USERS_FILE``; None if neither
    holds a valid list (logged), which means nobody can sign in."""
    text, where = os.environ.get(USERS_ENV), USERS_ENV
    if text is None:
        if not USERS_FILE.is_file():
            return None
        text, where = USERS_FILE.read_text(), str(USERS_FILE)
    try:
        users = json.loads(text)
    except ValueError:
        log.error("users: %s isn't valid JSON, so nobody can sign in", where)
        return None
    if not isinstance(users, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in users.items()):
        log.error("users: %s isn't a JSON object of email -> hash, so nobody can sign in", where)
        return None
    return {normalize(email): hashed for email, hashed in users.items()}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def setup_problem() -> str | None:
    """Why nobody can sign in right now, or None."""
    if not load_users():
        return ("No users are set up, so nobody can sign in. Whoever runs the site: "
                "see docs/website-setup.md, “Users”.")
    if os.environ.get("VERCEL") and not os.environ.get(SECRET_ENV):
        return (f"This site has no {SECRET_ENV}, so sign-ins can't be kept. Whoever runs the site: "
                "see docs/website-setup.md, “Users”.")
    return None


def check_password(email: str, password: str) -> str | None:
    """The user signing in (normalized email) if ``password`` is theirs, else None."""
    global _dummy_hash
    email = normalize(email)
    hashed = (load_users() or {}).get(email)
    if hashed is None:  # take as long as a real check, so timing doesn't reveal who's a user
        _dummy_hash = _dummy_hash or _hasher.hash(secrets.token_hex(16))
        hashed, email = _dummy_hash, None
    try:
        if _hasher.verify(hashed, password):
            return email
    except (VerificationError, InvalidHashError):
        pass
    return None


def make_cookie(user: str, now: float | None = None) -> str:
    """A signed cookie value naming ``user``, valid for ``MAX_AGE`` seconds."""
    expires = int((now or time.time()) + MAX_AGE)
    payload = base64.urlsafe_b64encode(json.dumps([user, expires]).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def user_from_cookie(value: str | None, now: float | None = None) -> str | None:
    """The signed-in user if ``value`` is a valid, unexpired cookie for someone still on the list."""
    if not value or "." not in value:
        return None
    payload, signature = value.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        user, expires = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    if expires < (now or time.time()) or user not in (load_users() or {}):
        return None
    return user


def may_build(user: str, source: SourceSpec) -> bool:
    """Whether ``user`` may build books from ``source``. Every user may use every site the
    website has a key for, for now; per-user sites would be decided here."""
    return True


def _sign(payload: str) -> str:
    secret = os.environ.get(SECRET_ENV, "").encode() or _local_secret
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
