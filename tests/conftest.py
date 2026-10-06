from __future__ import annotations

import io
import re
import shutil
import socket
import subprocess
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
