"""Writing to the store: its tables, its logins, loading data and exporting it (ADR 0004).

Used by ``scripts/store_admin.py`` on the maintainer's own machine, with the full-access
connection. The website never imports this. ``StoreData`` is both what gets loaded and what an
export file holds, so restoring a backup and the first import are the same operation.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from phoenix_ebook.errors import PhoenixError
from phoenix_ebook.secrets import MissingSecret, SecretStore, extract_domain

from web.store import Blog, SealedKey, seal

EXPORT_VERSION = 1
SITE_LOGIN = "phoenix_site"  # the website
CHECK_LOGIN = "phoenix_check"  # the development sandbox, for checking data and limits; no master key
READ_ONLY_LOGINS = (SITE_LOGIN, CHECK_LOGIN)

_TABLES = """
CREATE TABLE IF NOT EXISTS blogs (
    name text PRIMARY KEY,
    platform text NOT NULL,
    url text NOT NULL UNIQUE,
    hosts text[] NOT NULL,
    key_ciphertext bytea,
    key_master_id text
);
CREATE TABLE IF NOT EXISTS users (
    email text PRIMARY KEY,
    password_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS access (
    email text NOT NULL REFERENCES users ON DELETE CASCADE,
    blog text NOT NULL REFERENCES blogs ON DELETE CASCADE ON UPDATE CASCADE,
    PRIMARY KEY (email, blog)
);
"""


class StoreAdminError(PhoenixError):
    """Something the person running the script can fix; nothing has been written."""


@dataclass
class StoreData:
    """Everything in the store. Keys are always encrypted here."""

    blogs: list[tuple[Blog, SealedKey | None]] = field(default_factory=list)
    users: dict[str, str] = field(default_factory=dict)
    access: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- what to load

def read_users(path: Path) -> tuple[dict[str, str], str | None]:
    """The users in a ``~/.phoenix_users.json`` file, and what's wrong with it, if anything."""
    from web.auth import normalize
    if not path.is_file():
        return {}, f"the user list: {path} doesn't exist"
    try:
        users = json.loads(path.read_text())
    except ValueError:
        return {}, f"the user list: {path} isn't valid JSON"
    if not isinstance(users, dict) or not users:
        return {}, f"the user list: {path} has no users"
    bad = [email for email, value in users.items() if not (isinstance(value, str) and value.startswith("$argon2"))]
    if bad:
        return {}, f"the user list: {path} has no password hash for {', '.join(sorted(bad))}"
    return {normalize(email): value for email, value in users.items()}, None


def read_keys(blogs: tuple[Blog, ...], keys_path: Path | None) -> tuple[dict[str, str], list[str]]:
    """Each blog's key (by name), and what's missing. From ``keys_path`` if given (a file shaped like
    ``~/.phoenix_secrets.json``), else from wherever this machine's ``SecretStore`` finds them."""
    keys, missing = {}, []
    from_file = None
    if keys_path is not None:
        try:
            from_file = json.loads(keys_path.read_text())
        except (OSError, ValueError):
            return {}, [f"the keys: {keys_path} can't be read as JSON"]
    for blog in blogs:
        domain = extract_domain(blog.url)
        try:
            found = (from_file.get(blog.platform, {}).get(domain) if from_file is not None
                     else SecretStore().get(blog.platform, domain))
        except MissingSecret:
            found = None
        if found:
            keys[blog.name] = found
        else:
            missing.append(f"the key for {blog.name} ({blog.platform}, {domain})")
    return keys, missing


def data_from_files(blogs: tuple[Blog, ...], users_path: Path, keys_path: Path | None, master_key: str) -> StoreData:
    """What the first import loads. Current users get every blog. Raises ``StoreAdminError``
    naming everything missing, before anything has touched the store."""
    users, users_problem = read_users(users_path)
    keys, missing = read_keys(blogs, keys_path)
    if users_problem:
        missing.insert(0, users_problem)
    if missing:
        raise StoreAdminError("Nothing was written. Missing:\n  " + "\n  ".join(missing))
    return StoreData(
        blogs=[(blog, seal(keys[blog.name], blog, master_key)) for blog in blogs],
        users=users,
        access=[(email, blog.name) for email in users for blog in blogs],
    )


# ---------------------------------------------------------------- writing

def create_tables(conn: psycopg.Connection) -> None:
    conn.execute(_TABLES)


def master_key_ids(conn: psycopg.Connection) -> set[str]:
    """The master keys that encrypted what's in the store now."""
    rows = conn.execute("SELECT DISTINCT key_master_id FROM blogs WHERE key_master_id IS NOT NULL").fetchall()
    return {row[0] for row in rows}


def load(conn: psycopg.Connection, data: StoreData) -> None:
    """Make the store match ``data``. Run it in one transaction, so a failure leaves nothing changed."""
    conn.execute("DELETE FROM access")
    conn.execute("DELETE FROM users WHERE NOT (email = ANY(%s))", (list(data.users),))
    conn.execute("DELETE FROM blogs WHERE NOT (name = ANY(%s))", ([blog.name for blog, _ in data.blogs],))
    for blog, sealed in data.blogs:
        conn.execute(
            """INSERT INTO blogs (name, platform, url, hosts, key_ciphertext, key_master_id)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET platform = EXCLUDED.platform, url = EXCLUDED.url,
                 hosts = EXCLUDED.hosts, key_ciphertext = EXCLUDED.key_ciphertext,
                 key_master_id = EXCLUDED.key_master_id""",
            (blog.name, blog.platform, blog.url, list(blog.hosts),
             sealed.ciphertext if sealed else None, sealed.master_id if sealed else None))
    for email, password_hash in data.users.items():
        conn.execute(
            """INSERT INTO users (email, password_hash) VALUES (%s, %s)
               ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash""",
            (email, password_hash))
    for email, blog in data.access:
        conn.execute("INSERT INTO access (email, blog) VALUES (%s, %s)", (email, blog))


def set_up_logins(conn: psycopg.Connection, admin_url: str, new_passwords: bool = False) -> dict[str, str]:
    """Create the read-only logins and give them reading rights. Returns the connection address of
    each login whose password was just made: these can't be shown again."""
    made = {}
    for login in READ_ONLY_LOGINS:
        name = sql.Identifier(login)
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,)).fetchone()
        if not exists or new_passwords:
            password = secrets.token_urlsafe(32)
            statement = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if exists else "CREATE ROLE {} WITH LOGIN PASSWORD {}"
            conn.execute(sql.SQL(statement).format(name, sql.Literal(password)))
            made[login] = login_url(admin_url, login, password)
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(name))
        conn.execute(sql.SQL("GRANT SELECT ON blogs, users, access TO {}").format(name))
    return made


def login_url(admin_url: str, login: str, password: str) -> str:
    """``admin_url`` with its login swapped, so it reaches the same database by the same route."""
    parts = urlsplit(admin_url)
    if not parts.hostname:
        raise StoreAdminError("The full-access connection must be an address like "
                              "postgresql://user:password@host/database. Nothing was written.")
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(parts._replace(netloc=f"{quote(login, safe="")}:{quote(password, safe="")}@{parts.hostname}{port}"))


# ---------------------------------------------------------------- export and restore

def read_all(conn: psycopg.Connection) -> StoreData:
    blogs = [(Blog(name, platform, url, tuple(hosts)), SealedKey(bytes(ciphertext), master_id) if ciphertext else None)
             for name, platform, url, hosts, ciphertext, master_id in conn.execute(
                 "SELECT name, platform, url, hosts, key_ciphertext, key_master_id FROM blogs ORDER BY name")]
    users = dict(conn.execute("SELECT email, password_hash FROM users ORDER BY email").fetchall())
    access = [tuple(row) for row in conn.execute("SELECT email, blog FROM access ORDER BY email, blog").fetchall()]
    return StoreData(blogs, users, access)


def to_export(data: StoreData) -> str:
    """``data`` as an export file's text. Blog keys stay encrypted; emails and password hashes don't."""
    return json.dumps({
        "version": EXPORT_VERSION,
        "blogs": [{
            "name": blog.name, "platform": blog.platform, "url": blog.url, "hosts": list(blog.hosts),
            "key_ciphertext": base64.b64encode(sealed.ciphertext).decode() if sealed else None,
            "key_master_id": sealed.master_id if sealed else None,
        } for blog, sealed in data.blogs],
        "users": data.users,
        "access": [list(pair) for pair in data.access],
    }, indent=2, sort_keys=True) + "\n"


def from_export(text: str) -> StoreData:
    """The data in an export file's text."""
    try:
        raw = json.loads(text)
        if raw["version"] != EXPORT_VERSION:
            raise StoreAdminError(f"This export has version {raw['version']}; this script reads {EXPORT_VERSION}. "
                                  "Nothing was written.")
        return StoreData(
            blogs=[(Blog(b["name"], b["platform"], b["url"], tuple(b["hosts"])),
                    SealedKey(base64.b64decode(b["key_ciphertext"]), b["key_master_id"]) if b["key_ciphertext"] else None)
                   for b in raw["blogs"]],
            users=dict(raw["users"]),
            access=[(email, blog) for email, blog in raw["access"]],
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise StoreAdminError(f"This isn't a store export ({type(exc).__name__}). Nothing was written.") from None


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, readable only by its owner, in a folder only its owner can enter
    if the folder has to be made."""
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, parents=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)
