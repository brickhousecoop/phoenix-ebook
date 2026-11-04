#!/usr/bin/env python3
"""Manage the website's store: its users, blogs, keys and who builds from which blog, plus loading
and backing it up (ADR 0004, #40, #41). Run on your own computer, not on Vercel."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from phoenix_ebook.errors import PhoenixError  # noqa: E402
from web import auth, store, store_admin  # noqa: E402
from web.forms import BLOGS  # noqa: E402

ADMIN_URL_ENV = "PHOENIX_STORE_ADMIN_URL"
BACKUP_DIR = Path.home() / ".phoenix_store_backups"

EPILOG = f"""\
The full-access connection comes from {ADMIN_URL_ENV}, and the master key (for blog keys) from
{store.MASTER_KEY_ENV}; each is asked for if it isn't set. They stay on this computer and never go in
Vercel. Every change is backed up to {BACKUP_DIR}. See docs/website-setup.md, “The store”."""


def _admin_url() -> str:
    url = os.environ.get(ADMIN_URL_ENV) or getpass.getpass("Full-access connection address (won't show): ")
    if not url.strip():
        raise store_admin.StoreAdminError("The full-access connection is needed. Nothing was written.")
    return url.strip()


def _connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or _admin_url(), connect_timeout=30, prepare_threshold=None)


def _master_key() -> str:
    key = os.environ.get(store.MASTER_KEY_ENV) or getpass.getpass("Master key, from your password manager (won't show): ")
    store._parse_master_key(key)  # a mistyped key stops here, before anything is written
    return key.strip()


def _import(args: argparse.Namespace) -> int:
    master_key = os.environ.get(store.MASTER_KEY_ENV)
    generated = None
    if args.from_export:
        try:
            data = store_admin.from_export(Path(args.from_export).expanduser().read_text())
        except OSError as exc:
            raise store_admin.StoreAdminError(f"{args.from_export} can't be read ({exc.strerror}). "
                                              "Nothing was written.") from None
    else:
        if not master_key:
            generated = master_key = store.new_master_key()
        keys_file = Path(args.keys_file).expanduser() if args.keys_file else None
        data = store_admin.data_from_files(BLOGS, Path(args.users_file).expanduser(), keys_file, master_key)

    url = _admin_url()
    store_admin.login_url(url, "check", "check")  # a bad address stops here, before anything is written
    with _connect(url) as conn:  # one transaction: all of it, or nothing
        store_admin.create_tables(conn)
        if not args.from_export and not store_admin.is_empty(conn):
            raise store_admin.StoreAdminError(
                "The store already has users or blogs, and importing the lists would remove any added since. Use "
                "the user, blog, grant and revoke commands, or restore a backup with import --from-export. "
                "Nothing was written.")
        if generated and store_admin.master_key_ids(conn):
            raise store_admin.StoreAdminError(
                f"The store already has keys, but {store.MASTER_KEY_ENV} isn't set here. Set it to the value in your "
                "password manager and run this again. Nothing was written.")
        store_admin.load(conn, data)
        logins = store_admin.set_up_logins(conn, url)

    print(f"The store now has {len(data.blogs)} blogs and {len(data.users)} users.")
    if generated:
        print(f"\nMaster key (shown only this once). Put it in your password manager, and in Vercel as\n"
              f"{store.MASTER_KEY_ENV}, Sensitive. If it's lost, every blog's key has to be entered again:\n  {generated}")
    _show_logins(logins)
    return 0


def _show_logins(logins: dict[str, str]) -> None:
    for login, login_address in logins.items():
        purpose = (f"the website: put it in Vercel as {store.URL_ENV}, Sensitive" if login == store_admin.SITE_LOGIN
                   else "checking from the development sandbox: store it from your own shell, not in a chat")
        print(f"\nRead-only connection for {purpose} (shown only this once):\n  {login_address}")


def _new_login_passwords(args: argparse.Namespace) -> int:
    url = _admin_url()
    with _connect(url) as conn:
        _show_logins(store_admin.set_up_logins(conn, url, new_passwords=True))
    print(f"\nThe old ones stop working now: replace {store.URL_ENV} in Vercel and redeploy.")
    return 0


def _backup_path() -> Path:
    return BACKUP_DIR / f"{datetime.now():%Y-%m-%d-%H%M%S}.json"


def _export(args: argparse.Namespace) -> int:
    path = Path(args.to).expanduser() if args.to else _backup_path()
    url = os.environ.get(ADMIN_URL_ENV) or os.environ.get(store.URL_ENV) or _admin_url()
    with psycopg.connect(url, connect_timeout=30, prepare_threshold=None) as conn:
        data = store_admin.read_all(conn)
    store_admin.write_private(path, store_admin.to_export(data))
    print(f"Exported {len(data.blogs)} blogs and {len(data.users)} users to {path}.\n"
          "It holds emails and password hashes, so keep it private. Blog keys stay encrypted in it.")
    return 0


def _change(change, *, master_key: bool = False) -> int:
    """Run ``change(conn[, master_key])`` in one transaction, then back up the store if it changed.
    ``change`` returns what to print, and whether anything changed."""
    key = _master_key() if master_key else None
    with _connect() as conn:
        store_admin.create_tables(conn)  # adds anything later versions need, such as short names
        message, changed = change(conn, key) if master_key else change(conn)
        conn.commit()
        path = _backup_path()
        if changed:  # every change is backed up straight away: Neon's free plan keeps no backups of its own
            store_admin.write_private(path, store_admin.to_export(store_admin.read_all(conn)))
    print(message)
    if changed:
        print(f"Backed up to {path}.")
    return 0


def _change_access(args: argparse.Namespace) -> int:
    def change(conn):
        if args.command == "grant":
            email, blog, changed = store_admin.grant(conn, args.email, args.blog)
            return (f"{email} can now build from {blog}." if changed
                    else f"{email} could already build from {blog}."), changed
        email, blog, changed = store_admin.revoke(conn, args.email, args.blog)
        return (f"{email} can no longer build from {blog}, from their next page on." if changed
                else f"{email} couldn't build from {blog} anyway."), changed
    return _change(change)


def _password_message(email: str, password: str) -> str:
    return f"Password for {email} (shown only this once; send it to them privately):\n  {password}"


def _user(args: argparse.Namespace) -> int:
    if args.action == "list":
        return _users(args)
    if args.action == "add":
        def change(conn):
            email, password = store_admin.add_user(conn, args.email)
            return (_password_message(email, password) + f"\nThey can't build from any blog until you grant one: "
                    f"grant {email} BLOG"), True
    elif args.action == "new-password":
        def change(conn):
            email, password = store_admin.new_password(conn, args.email)
            return _password_message(email, password) + "\nTheir old password stops working now.", True
    else:
        def change(conn):
            return f"Removed {store_admin.remove_user(conn, args.email)}, with their blogs. They're signed out at " \
                   "their next page.", True
    return _change(change)


def _blog_key(name: str) -> str:
    return getpass.getpass(f"Key for {name} (won't show): ")


def _blog(args: argparse.Namespace) -> int:
    if args.action == "add":
        hosts = tuple(h.strip().lower() for h in (args.hosts or "").split(",") if h.strip()) or \
            (store_admin.urlsplit(args.url).hostname or "",)
        url = args.url.rstrip("/")
        blog = store.Blog(args.name, args.platform, url, hosts,
                          (args.short_name or store_admin.default_short_name(url)).strip().lower())

        def change(conn, master_key):
            store_admin.add_blog(conn, blog, _blog_key(blog.name), master_key)
            return (f"Added {blog.name} ({blog.short_name}), for addresses on {', '.join(blog.hosts)}. Nobody can "
                    f"build from it until you grant it: grant EMAIL {blog.short_name}"), True
    elif args.action == "list":
        return _blogs(args)
    elif args.action == "short-name":
        def change(conn):
            name, changed = store_admin.set_short_name(conn, args.blog, args.short_name)
            return (f"{name} is now {args.short_name.strip().lower()}." if changed
                    else f"{name} already had that short name."), changed
        return _change(change)
    else:
        def change(conn, master_key):
            name = store_admin.find_blog(conn, args.blog)
            store_admin.set_key(conn, name, _blog_key(name), master_key)
            return f"Stored a new key for {name}; builds use it from now on.", True
    return _change(change, master_key=True)


def _blogs(args: argparse.Namespace) -> int:
    url = os.environ.get(ADMIN_URL_ENV) or os.environ.get(store.URL_ENV) or _admin_url()
    with _connect(url) as conn:
        blogs = store_admin.read_all(conn).blogs
    for blog, sealed in blogs:
        print(f"{blog.short_name or '-':<14}  {blog.name}  ({', '.join(blog.hosts)}; key: {'stored' if sealed else 'none'})")
    return 0


def _users(args: argparse.Namespace) -> int:
    url = os.environ.get(ADMIN_URL_ENV) or os.environ.get(store.URL_ENV) or _admin_url()
    with _connect(url) as conn:
        users = store_admin.users_and_blogs(conn)
    width = max(map(len, users), default=0)
    for email, blogs in users.items():
        print(f"{email:<{width}}  {', '.join(blogs) or '(no blogs)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("import", help="load an empty store from the user list, the keys and the blogs in the "
                                              "code (everyone gets every blog), or restore an export over everything")
    load.add_argument("--users-file", default=str(auth.USERS_FILE), metavar="PATH",
                      help=f"the user list (default: {auth.USERS_FILE})")
    load.add_argument("--keys-file", metavar="PATH", help="a file shaped like ~/.phoenix_secrets.json "
                                                          "(default: wherever this computer keeps its keys)")
    load.add_argument("--from-export", metavar="PATH", help="restore this export instead of reading the lists")
    load.set_defaults(run=_import)

    export = commands.add_parser("export", help="save everything in the store to a private file")
    export.add_argument("--to", metavar="PATH", help=f"where (default: a dated file in {BACKUP_DIR})")
    export.set_defaults(run=_export)

    for name, verb in (("grant", "let a user build from a blog"), ("revoke", "stop a user building from a blog")):
        access = commands.add_parser(name, help=verb)
        access.add_argument("email")
        access.add_argument("blog", help="its short name (like hellworld), its name or one of its hosts")
        access.set_defaults(run=_change_access)

    users = commands.add_parser("users", help="list the users and the blogs each may build from")
    users.set_defaults(run=_users)

    user = commands.add_parser("user", help="add or remove a user, or give them a new password")
    user_actions = user.add_subparsers(dest="action", required=True)
    for action, verb in (("add", "add a user, with a generated password and no blogs"),
                         ("new-password", "give a user a new generated password"),
                         ("remove", "remove a user and their blogs")):
        user_actions.add_parser(action, help=verb).add_argument("email")
    user_actions.add_parser("list", help="the same as users")
    user.set_defaults(run=_user)

    blog = commands.add_parser("blog", help="add or list blogs, replace a key, or change a short name")
    blog_actions = blog.add_subparsers(dest="action", required=True)
    add = blog_actions.add_parser("add", help="add a blog; asks for its key")
    add.add_argument("name", help='what the website calls it, like "Welcome to Hell World"')
    add.add_argument("--url", required=True, help="where its API is, like https://hellworld.ghost.io")
    add.add_argument("--hosts", help="the hosts its post addresses use, public one first, comma-separated "
                                     "(default: the --url host)")
    add.add_argument("--platform", default="ghost", help="default: ghost")
    add.add_argument("--short-name", help="what these commands call it, like hellworld (default: the first part "
                                          "of the --url host)")
    short = blog_actions.add_parser("short-name", help="change what these commands call a blog")
    short.add_argument("blog", help="its current short name, its name or one of its hosts")
    short.add_argument("short_name", help="lowercase letters, digits and hyphens")
    blog_actions.add_parser("list", help="every blog, with its short name and hosts")
    set_key = blog_actions.add_parser("set-key", help="replace a blog's key; asks for it")
    set_key.add_argument("blog", help="its short name (like hellworld), its name or one of its hosts")
    blog.set_defaults(run=_blog)

    logins = commands.add_parser("new-login-passwords", help="give the read-only logins new passwords and show them")
    logins.set_defaults(run=_new_login_passwords)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except PhoenixError as exc:  # StoreAdminError, a bad master key, an unknown platform
        print(f"error: {exc}", file=sys.stderr)
    except psycopg.Error as exc:
        print(f"error: the store refused or couldn't be reached ({type(exc).__name__}). Nothing was written. "
              f"{exc.diag.message_primary or ''}".rstrip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
