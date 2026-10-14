#!/usr/bin/env python3
"""Add, reset or remove a website user (ADR 0003), and print the PHOENIX_USERS value for Vercel."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import auth  # noqa: E402

EPILOG = f"""\
The live site reads its users from the {auth.USERS_ENV} environment variable on Vercel, which
holds the real list; ~/.phoenix_users.json is a copy kept on this machine. If two people
manage users from different machines, pasting one copy can undo the other's changes.
This script never contacts Vercel."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Give a website user a new random password (adding them if they're new), or remove them.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email", help="the user's email address")
    parser.add_argument("--remove", action="store_true", help="remove the user instead")
    parser.add_argument("--users-file", default=str(auth.USERS_FILE), metavar="PATH",
                        help=f"the list to change (default: {auth.USERS_FILE}); '-' reads it from standard "
                             "input and writes the changed list to standard output")
    args = parser.parse_args(argv)
    email = auth.normalize(args.email)
    piped = args.users_file == "-"
    path = Path(args.users_file).expanduser()

    try:
        text = sys.stdin.read() if piped else (path.read_text() if path.exists() else "{}")
        users = {auth.normalize(k): v for k, v in json.loads(text or "{}").items()}
    except (ValueError, AttributeError):
        print(f"error: {'standard input' if piped else path} isn't a JSON object of email -> hash", file=sys.stderr)
        return 1

    password = None
    if args.remove:
        if users.pop(email, None) is None:
            print(f"error: {email} isn't a user", file=sys.stderr)
            return 1
    else:
        password = secrets.token_urlsafe(18)
        users[email] = auth.hash_password(password)

    value = json.dumps(users, sort_keys=True, separators=(",", ":"))
    if piped:
        print(value)
        out = sys.stderr
    else:
        _write_private(path, json.dumps(users, sort_keys=True, indent=2) + "\n")
        out = sys.stdout
    if password:
        print(f"Password for {email} (shown only this once; send it to them privately):\n  {password}", file=out)
    else:
        print(f"Removed {email}.", file=out)
    if not piped:
        print(f"\n{auth.USERS_ENV} for Vercel (Settings → Environment Variables, Sensitive), then redeploy:\n{value}",
              file=out)
    return 0


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, readable only by its owner."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


if __name__ == "__main__":
    sys.exit(main())
