"""Where finished books go: Vercel Blob when deployed, a local folder otherwise (ADR 0002).

Each build gets an unguessable folder (128 random bits) holding the book and the
files uploaded for it, so a rebuild can reuse the uploads (a browser can't refill
a file input). Anyone with a link can download from it. Nothing expires yet.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

BLOB_TOKEN_ENV = "BLOB_READ_WRITE_TOKEN"  # set by Vercel when a Blob store is connected
BLOB_HOST_SUFFIX = ".public.blob.vercel-storage.com"
EPUB_TYPE = "application/epub+zip"
LOCAL_DIR = Path(os.environ.get("PHOENIX_BOOKS_DIR") or Path(tempfile.gettempdir()) / "phoenix-books")
LOCAL_URL_PREFIX = "/books"


def setup_problem() -> str | None:
    """Why books can't be stored right now, or None. On Vercel the local folder vanishes with the
    function, so Blob is required there; Vercel sets ``VERCEL=1`` in its functions."""
    if os.environ.get("VERCEL") and not os.environ.get(BLOB_TOKEN_ENV):
        return (f"This site isn't connected to its Blob store ({BLOB_TOKEN_ENV} isn't set), so a book couldn't "
                "be saved for download. Whoever runs the site: see docs/website-setup.md, “Blob store”.")
    return None


def download_name(title: str) -> str:
    """'Flaming Hydra: September 2026' -> 'Flaming-Hydra-September-2026.epub'."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:80]
    return f"{stem or 'book'}.epub"


def new_folder() -> str:
    """A fresh unguessable folder name for one build's files."""
    return secrets.token_urlsafe(16)


def save_book(path: str, title: str, folder: str) -> str:
    """Store the EPUB at ``path`` in ``folder``; the address to download it from."""
    return _save(path, f"{folder}/{download_name(title)}", EPUB_TYPE)


def save_upload(path: str, name: str, folder: str) -> str:
    """Keep an uploaded file in ``folder`` as ``name`` ('<field>/<file name>'); a reference for ``load_upload``."""
    return _save(path, f"{folder}/uploads/{name}", None)


def load_upload(ref: str) -> tuple[bytes, str] | None:
    """The bytes and file name of a file kept by ``save_upload``, or None if ``ref`` isn't one."""
    parts = urlsplit(ref)
    name = parts.path.rsplit("/", 1)[-1]
    if not parts.scheme and ref.startswith(LOCAL_URL_PREFIX + "/"):
        path = local_book(ref[len(LOCAL_URL_PREFIX) + 1:])
        return (path.read_bytes(), name) if path and path.parent.parent.name == "uploads" else None
    if parts.scheme == "https" and parts.hostname and parts.hostname.endswith(BLOB_HOST_SUFFIX) \
            and re.fullmatch(r"/books/[\w-]+/uploads/[a-z_]+/[^/]+", parts.path):
        response = requests.get(ref, timeout=30)
        return (response.content, name) if response.status_code == 200 else None
    return None


def local_book(key: str) -> Path | None:
    """The locally saved file for ``key`` ('<folder>/<name>' or '<folder>/uploads/<field>/<name>'), or None."""
    target = (LOCAL_DIR / key).resolve()
    if LOCAL_DIR.resolve() not in target.parents or not target.is_file():
        return None
    return target


def _save(path: str, key: str, content_type: str | None) -> str:
    if os.environ.get(BLOB_TOKEN_ENV):
        from vercel.blob import upload_file

        kwargs = {"content_type": content_type} if content_type else {}
        result = upload_file(path, f"books/{key}", access="public", multipart=True, **kwargs)
        return result.download_url if content_type == EPUB_TYPE else result.url
    target = LOCAL_DIR / key
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    return f"{LOCAL_URL_PREFIX}/{key}"
