#!/usr/bin/env python3
"""Load the website's store, back it up, and choose who builds from which blog (ADR 0004, #41). Run on your
own computer, not on Vercel."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from web import auth, store, store_admin  # noqa: E402
from web.forms import BLOGS  # noqa: E402

ADMIN_URL_ENV = "PHOENIX_STORE_ADMIN_URL"
BACKUP_DIR = Path.home() / ".phoenix_store_backups"

EPILOG = f"""\
The full-access connection comes from {ADMIN_URL_ENV}, or is asked for. It stays on this computer and
never goes in Vercel. See docs/website-setup.md, “The store”."""


def _admin_url() -> str:
    url = os.environ.get(ADMIN_URL_ENV) or getpass.getpass("Full-access connection address (won't show): ")
    if not url.strip():
        raise store_admin.StoreAdminError("The full-access connection is needed. Nothing was written.")
    return url.strip()


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
    with psycopg.connect(url, connect_timeout=30, prepare_threshold=None) as conn:  # one transaction: all of it, or nothing
        store_admin.create_tables(conn)
        if generated and store_admin.master_key_ids(conn):
            raise store_admin.StoreAdminError(
                f"The store already has keys, but {store.MASTER_KEY_ENV} isn't set here. Set it to the value in your "
                "password manager and run this again. Nothing was written.")
        if not args.from_export:
            data = store_admin.access_to_keep(conn, data)  # users keep their blogs; new ones start with none
        store_admin.load(conn, data)
        logins = store_admin.set_up_logins(conn, url, new_passwords=args.new_login_passwords)

    print(f"The store now has {len(data.blogs)} blogs and {len(data.users)} users.")
    if generated:
        print(f"\nMaster key (shown only this once). Put it in your password manager, and in Vercel as\n"
              f"{store.MASTER_KEY_ENV}, Sensitive. If it's lost, every blog's key has to be entered again:\n  {generated}")
    for login, login_address in logins.items():
        purpose = (f"the website: put it in Vercel as {store.URL_ENV}, Sensitive" if login == store_admin.SITE_LOGIN
                   else "checking from the development sandbox: store it from your own shell, not in a chat")
        print(f"\nRead-only connection for {purpose} (shown only this once):\n  {login_address}")
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


def _change_access(args: argparse.Namespace) -> int:
    change = store_admin.grant if args.command == "grant" else store_admin.revoke
    with psycopg.connect(_admin_url(), connect_timeout=30, prepare_threshold=None) as conn:
        email, blog, changed = change(conn, args.email, args.blog)
        conn.commit()
        path = _backup_path()
        if changed:  # every change is backed up straight away: Neon's free plan keeps no backups of its own
            store_admin.write_private(path, store_admin.to_export(store_admin.read_all(conn)))
    if args.command == "grant":
        print(f"{email} can now build from {blog}." if changed else f"{email} could already build from {blog}.")
    else:
        print(f"{email} can no longer build from {blog}, from their next page on." if changed
              else f"{email} couldn't build from {blog} anyway.")
    if changed:
        print(f"Backed up to {path}.")
    return 0


def _users(args: argparse.Namespace) -> int:
    url = os.environ.get(ADMIN_URL_ENV) or os.environ.get(store.URL_ENV) or _admin_url()
    with psycopg.connect(url, connect_timeout=30, prepare_threshold=None) as conn:
        users = store_admin.users_and_blogs(conn)
    width = max(map(len, users), default=0)
    for email, blogs in users.items():
        print(f"{email:<{width}}  {', '.join(blogs) or '(no blogs)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("import", help="make the store match the user list, the keys and the blogs in the code, "
                                              "or an export; removes whatever they don't list. Users keep their "
                                              "blogs; new users start with none")
    load.add_argument("--users-file", default=str(auth.USERS_FILE), metavar="PATH",
                      help=f"the user list (default: {auth.USERS_FILE})")
    load.add_argument("--keys-file", metavar="PATH", help="a file shaped like ~/.phoenix_secrets.json "
                                                          "(default: wherever this computer keeps its keys)")
    load.add_argument("--from-export", metavar="PATH", help="restore this export instead of reading the lists")
    load.add_argument("--new-login-passwords", action="store_true",
                      help="give the read-only logins new passwords and show them")
    load.set_defaults(run=_import)

    export = commands.add_parser("export", help="save everything in the store to a private file")
    export.add_argument("--to", metavar="PATH", help=f"where (default: a dated file in {BACKUP_DIR})")
    export.set_defaults(run=_export)

    for name, verb in (("grant", "let a user build from a blog"), ("revoke", "stop a user building from a blog")):
        access = commands.add_parser(name, help=verb)
        access.add_argument("email")
        access.add_argument("blog", help="its name, one of its addresses' hosts, or a short name like hellworld")
        access.set_defaults(run=_change_access)

    users = commands.add_parser("users", help="list the users and the blogs each may build from")
    users.set_defaults(run=_users)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except store_admin.StoreAdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
    except psycopg.Error as exc:
        print(f"error: the store refused or couldn't be reached ({type(exc).__name__}). Nothing was written. "
              f"{exc.diag.message_primary or ''}".rstrip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
