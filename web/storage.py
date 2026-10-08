"""Where finished books go: Vercel Blob when deployed, a local folder otherwise (ADR 0002).

Each book gets an unguessable folder (128 random bits) so a link can't be found
by guessing; anyone with the link can download it. Nothing expires yet.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path

BLOB_TOKEN_ENV = "BLOB_READ_WRITE_TOKEN"  # set by Vercel when a Blob store is connected
EPUB_TYPE = "application/epub+zip"
LOCAL_DIR = Path(os.environ.get("PHOENIX_BOOKS_DIR") or Path(tempfile.gettempdir()) / "phoenix-books")
LOCAL_URL_PREFIX = "/books"


def download_name(title: str) -> str:
    """'Flaming Hydra: September 2026' -> 'Flaming-Hydra-September-2026.epub'."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:80]
    return f"{stem or 'book'}.epub"


def save_book(path: str, title: str) -> str:
    """Store the EPUB at ``path`` and return the address it can be downloaded from."""
    key = f"{secrets.token_urlsafe(16)}/{download_name(title)}"
    if os.environ.get(BLOB_TOKEN_ENV):
        return _save_to_blob(path, f"books/{key}")
    target = LOCAL_DIR / key
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    return f"{LOCAL_URL_PREFIX}/{key}"


def local_book(key: str) -> Path | None:
    """The locally saved book for ``key`` ('<token>/<name>.epub'), or None."""
    target = (LOCAL_DIR / key).resolve()
    if LOCAL_DIR.resolve() not in target.parents or not target.is_file():
        return None
    return target


def _save_to_blob(path: str, pathname: str) -> str:
    from vercel.blob import upload_file

    result = upload_file(path, pathname, access="public", content_type=EPUB_TYPE, multipart=True)
    return result.download_url
