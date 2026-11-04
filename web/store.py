"""The website's store: blogs and their keys, users and their access, in Postgres (ADR 0004).

With ``PHOENIX_STORE_URL`` set, the store is the only source of all of these, and the old
variables are ignored. Without it, the website uses what it used before: ``BLOGS``,
``PHOENIX_USERS`` or ``~/.phoenix_users.json``, and ``SecretStore``.

The website only reads, with a read-only login, and keeps no copy: every page asks again, so a
removed user or a replaced key takes effect on the next request. Only ``web/store_admin.py``
writes. The library knows nothing about any of this.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
from dataclasses import dataclass

import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from phoenix_ebook.errors import PhoenixError

URL_ENV = "PHOENIX_STORE_URL"
MASTER_KEY_ENV = "PHOENIX_STORE_MASTER_KEY"
CONNECT_TIMEOUT = 10  # seconds; covers the database waking from sleep

TRY_AGAIN = "This site can't reach its database right now. Nothing is wrong with your account. Try again in a minute."

log = logging.getLogger(__name__)
_NONCE_BYTES = 12


class StoreUnavailable(Exception):
    """The store couldn't be read. Never means "no such user" or "no such blog"."""


class StoreKeyProblem(PhoenixError):
    """A blog's key can't be used. Whoever runs the site has to fix it; the message says how."""


@dataclass(frozen=True)
class Blog:
    """A blog the website builds from. A post's full address says which blog it's on."""

    name: str
    platform: str
    url: str  # where its platform's API is
    hosts: tuple[str, ...]  # exact hosts its post addresses may use; the public one first
    short_name: str = ""  # what the scripts call it, like hellworld; the website doesn't read it


@dataclass(frozen=True)
class SealedKey:
    """A blog's key, encrypted. ``master_id`` says which master key encrypted it."""

    ciphertext: bytes
    master_id: str


class Store:
    """Reads the store. Every method opens its own short connection, because the site runs as
    short-lived functions behind a connection pooler."""

    def __init__(self, url: str) -> None:
        self._url = url

    def _rows(self, query: str, params: tuple = ()) -> list[tuple]:
        try:
            # prepare_threshold=None: the pooler may give each statement a different connection.
            with psycopg.connect(self._url, connect_timeout=CONNECT_TIMEOUT, autocommit=True,
                                 prepare_threshold=None) as conn:
                return conn.execute(query, params).fetchall()
        except psycopg.Error as exc:
            log.error("store: couldn't read it (%s)", type(exc).__name__)  # only the type: never the address
            raise StoreUnavailable from None

    def users(self) -> dict[str, str]:
        """Email -> argon2id hash."""
        return dict(self._rows("SELECT email, password_hash FROM users"))

    def blogs(self) -> tuple[Blog, ...]:
        rows = self._rows("SELECT name, platform, url, hosts FROM blogs ORDER BY name")
        return tuple(Blog(name, platform, url, tuple(hosts)) for name, platform, url, hosts in rows)

    def blogs_and_access(self, email: str) -> tuple[tuple[Blog, ...], frozenset[str]]:
        """Every blog, and the names of those ``email`` may build from, in one read."""
        rows = self._rows("""SELECT b.name, b.platform, b.url, b.hosts, a.email IS NOT NULL
                             FROM blogs b LEFT JOIN access a ON a.blog = b.name AND a.email = %s
                             ORDER BY b.name""", (email,))
        return (tuple(Blog(name, platform, url, tuple(hosts)) for name, platform, url, hosts, _ in rows),
                frozenset(name for name, _, _, _, allowed in rows if allowed))

    def sealed_key(self, blog: Blog) -> SealedKey | None:
        rows = self._rows("SELECT key_ciphertext, key_master_id FROM blogs WHERE name = %s", (blog.name,))
        return SealedKey(bytes(rows[0][0]), rows[0][1]) if rows and rows[0][0] else None


def _open(url: str) -> Store:
    return Store(url)


def active() -> Store | None:
    """The store, if this site is connected to one; None means it uses the old sources."""
    url = os.environ.get(URL_ENV)
    return _open(url) if url else None


def setup_problem() -> str | None:
    """Why a connected store can't work, or None."""
    if os.environ.get(URL_ENV) and not os.environ.get(MASTER_KEY_ENV):
        return (f"This site has {URL_ENV} but no {MASTER_KEY_ENV}, so it can't use any blog's key. "
                "Whoever runs the site: see docs/website-setup.md, “The store”.")
    return None


# ---------------------------------------------------------------- blog keys

def new_master_key() -> str:
    """A new master key, as text: an id anyone may see, then 32 random bytes."""
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    return f"{secrets.token_hex(4)}.{key}"


def _parse_master_key(text: str) -> tuple[str, bytes]:
    master_id, _, encoded = text.strip().partition(".")
    try:
        key = base64.urlsafe_b64decode(encoded)
    except ValueError:
        key = b""
    if not master_id or len(key) != 32:
        raise StoreKeyProblem(f"{MASTER_KEY_ENV} isn't a master key. Whoever runs the site: put back the value "
                              "from the password manager.")
    return master_id, key


def _associated_data(blog: Blog) -> bytes:
    """What an encrypted key is tied to, so it won't decrypt if it's copied to another blog's row."""
    return f"{blog.platform}\0{blog.url}".encode()


def seal(secret: str, blog: Blog, master_key: str) -> SealedKey:
    """Encrypt ``blog``'s key with AES-256-GCM."""
    master_id, key = _parse_master_key(master_key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    return SealedKey(nonce + AESGCM(key).encrypt(nonce, secret.encode(), _associated_data(blog)), master_id)


def unseal(sealed: SealedKey, blog: Blog, master_key: str) -> str:
    """``blog``'s key, or ``StoreKeyProblem`` if this master key or this blog isn't the right one."""
    master_id, key = _parse_master_key(master_key)
    if sealed.master_id != master_id:
        raise StoreKeyProblem(f"The key for {blog.name} was encrypted with master key {sealed.master_id}, but this "
                              f"site has {master_id}. Whoever runs the site: restore the right {MASTER_KEY_ENV}.")
    nonce, ciphertext = sealed.ciphertext[:_NONCE_BYTES], sealed.ciphertext[_NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _associated_data(blog)).decode()
    except InvalidTag:
        raise StoreKeyProblem(f"The stored key for {blog.name} can't be decrypted: it was changed, or it belongs to "
                              "another blog. Whoever runs the site: store this blog's key again.") from None


def blog_key(store: Store, blog: Blog) -> str:
    """``blog``'s key from the store, decrypted."""
    sealed = store.sealed_key(blog)
    if sealed is None:
        raise StoreKeyProblem(f"This site has no key for {blog.name}. Whoever runs the site: store one.")
    master_key = os.environ.get(MASTER_KEY_ENV)
    if not master_key:
        raise StoreKeyProblem(setup_problem() or f"{MASTER_KEY_ENV} isn't set.")
    return unseal(sealed, blog, master_key)
