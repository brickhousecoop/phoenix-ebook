from __future__ import annotations

import io
import json
import os
import re
import shutil
import socket
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
import requests
from PIL import Image

from phoenix_ebook import images
from phoenix_ebook.epub_builder import build
from phoenix_ebook.models import BookSpec, Post
from phoenix_ebook.processors.base import get_processor

REPO = Path(__file__).resolve().parent.parent
EPUBCHECK_JAR = REPO / ".tools" / "epubcheck-5.4.0" / "epubcheck.jar"


# ---------------------------------------------------------------- guards

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The suite is offline: any real connection attempt is a test bug."""
    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(autouse=True)
def no_stored_secrets(monkeypatch, tmp_path):
    """Keys stored on this machine (file, keyring, environment) never reach a test."""
    from phoenix_ebook import secrets
    monkeypatch.setattr(secrets, "SECRET_FILE", tmp_path / "phoenix_secrets.json")
    monkeypatch.setattr(secrets, "LEGACY_GHOST_SECRET_FILE", tmp_path / "ghost_epub_secrets.json")
    monkeypatch.setattr(secrets, "keyring", None)
    for name in [n for n in os.environ if n.startswith("PHOENIX_SECRET_")]:
        monkeypatch.delenv(name)


TEST_USER = "tester@example.org"
TEST_PASSWORD = "correct horse battery staple"
_test_users: str | None = None  # hashing takes a moment, so once per run


def users_json() -> str:
    """A ``PHOENIX_USERS`` value holding just ``TEST_USER``."""
    global _test_users
    if _test_users is None:
        from web.auth import hash_password
        _test_users = json.dumps({TEST_USER: hash_password(TEST_PASSWORD)})
    return _test_users


@pytest.fixture(autouse=True)
def no_stored_users(monkeypatch, tmp_path):
    """Website users stored on this machine never reach a test; tests get ``TEST_USER`` instead."""
    try:
        from web import auth
    except ImportError:  # the website's dependencies aren't installed: library tests still run
        return
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "phoenix_users.json")
    monkeypatch.delenv(auth.SECRET_ENV, raising=False)
    from web import store
    monkeypatch.delenv(store.URL_ENV, raising=False)
    monkeypatch.delenv(store.MASTER_KEY_ENV, raising=False)
    monkeypatch.setenv(auth.USERS_ENV, users_json())


def web_client(user: str | None = TEST_USER):
    """A client for the website, sending forms the way a browser on the site does,
    and signed in as ``user`` (None: signed out)."""
    from fastapi.testclient import TestClient
    from web import auth
    from web.main import app
    client = TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"})
    if user:
        client.cookies.set(auth.COOKIE, auth.make_cookie(user))
    return client


POSTGRES_IMAGE = "postgres:17-alpine"


@pytest.fixture(scope="session")
def postgres_server():
    """The address of a throwaway Postgres on this machine, for the few tests that need the real
    thing. ``PHOENIX_TEST_POSTGRES_URL`` names one; otherwise Docker runs one, if it has the image.
    Nothing is downloaded and nothing leaves this machine; without either, those tests skip."""
    if url := os.environ.get("PHOENIX_TEST_POSTGRES_URL"):
        yield url
        return
    docker = shutil.which("docker")
    if not docker or subprocess.run([docker, "image", "inspect", POSTGRES_IMAGE], capture_output=True).returncode:
        pytest.skip(f"no Postgres: run `docker pull {POSTGRES_IMAGE}` or set PHOENIX_TEST_POSTGRES_URL")
    started = subprocess.run(
        [docker, "run", "-d", "--rm", "-e", "POSTGRES_PASSWORD=test", "-p", "127.0.0.1::5432", POSTGRES_IMAGE],
        capture_output=True, text=True)
    if started.returncode:
        pytest.skip(f"Docker couldn't start Postgres: {started.stderr.strip()}")
    container = started.stdout.strip()
    try:
        port = subprocess.run([docker, "port", container, "5432/tcp"], capture_output=True,
                              text=True).stdout.split(":")[-1].strip()
        # Over TCP: while it first sets itself up, Postgres only listens on its local socket.
        deadline = time.monotonic() + 60
        while subprocess.run([docker, "exec", container, "pg_isready", "-h", "127.0.0.1"],
                             capture_output=True).returncode:
            if time.monotonic() > deadline:
                pytest.skip("Docker's Postgres didn't become ready")
        yield f"postgresql://postgres:test@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([docker, "stop", "-t", "0", container], capture_output=True)


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(images.time, "sleep", lambda seconds: None)


# ---------------------------------------------------------------- images

def png(color="red", size=(40, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def jpeg(color="red", size=(40, 30), **save_kwargs) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", **save_kwargs)
    return buf.getvalue()


def encode(img: Image.Image, fmt: str, **save_kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


# ---------------------------------------------------------------- HTTP

class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream",
                 url: str = "", history: list | None = None):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": content_type}
        self.url = url
        self.history = history or []

    def json(self):
        import json
        return json.loads(self.content)


class FakeSession(requests.Session):
    """Scripted responses per URL.

    Each route is a list of outcomes consumed one per request (the last one
    repeats): an exception instance to raise, or a FakeResponse.
    """

    def __init__(self, routes: dict[str, list] | None = None):
        super().__init__()
        self.routes = routes or {}
        self.calls: dict[str, int] = {}
        self.log: list[tuple[str, dict]] = []  # every request, in order

    def get(self, url, **kwargs):
        self.log.append((url, kwargs))
        self.calls[url] = self.calls.get(url, 0) + 1
        outcomes = self.routes[url]
        outcome = outcomes[min(self.calls[url], len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def ok(body: bytes, content_type: str = "image/png") -> FakeResponse:
    return FakeResponse(200, body, content_type)


# ---------------------------------------------------------------- building

class Built:
    """A built book, opened for inspection."""

    def __init__(self, result, path: Path):
        self.result = result
        self.path = path
        self.zip = zipfile.ZipFile(path)

    def text(self, name: str) -> str:
        return self.zip.read(f"EPUB/{name}").decode()

    def chapter(self, slug: str = "p") -> str:
        return self.text(f"{slug}.xhtml")

    def image_names(self) -> list[str]:
        return [n for n in self.zip.namelist() if n.startswith("EPUB/images/")]


@pytest.fixture
def build_book(tmp_path):
    """build_book(posts_or_post, routes, **spec_fields) -> Built"""
    counter = iter(range(10_000))

    def _build(posts, routes=None, *, session=None, processor="generic", image_base_url="", platform=None, **spec_fields):
        if isinstance(posts, Post):
            posts = [posts]
        out = tmp_path / f"book{next(counter)}.epub"
        session = session or FakeSession(routes)
        spec = BookSpec(title=spec_fields.pop("title", "t"), output=str(out), **spec_fields)
        result = build(spec, posts, get_processor(processor), session=session, image_base_url=image_base_url,
                       platform=platform)
        built = Built(result, out)
        built.session = session
        return built

    return _build


def post(html: str = "<p>Body.</p>", **fields) -> Post:
    return Post(slug=fields.pop("slug", "p"), title=fields.pop("title", "T"), html=html, **fields)


# ---------------------------------------------------------------- epubcheck

def run_epubcheck(path: Path) -> str:
    """Return epubcheck's 'Messages: …' summary line; skip if unavailable."""
    if shutil.which("java") is None:
        pytest.skip("epubcheck needs Java, which is not installed")
    if not EPUBCHECK_JAR.exists():
        pytest.skip("epubcheck jar missing; run scripts/fetch_epubcheck.py")
    out = subprocess.run(["java", "-jar", str(EPUBCHECK_JAR), str(path)], capture_output=True, text=True)
    match = re.search(r"Messages: .*", out.stdout + out.stderr)
    assert match, out.stdout + out.stderr
    return match.group(0)


def assert_valid_epub(path: Path) -> None:
    summary = run_epubcheck(path)
    assert "0 fatals / 0 errors / 0 warnings" in summary, summary
