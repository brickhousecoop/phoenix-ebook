"""Building from the website: progress, storage, results page, failures (#24)."""
from __future__ import annotations

import json
import time

import pytest
import requests
from fastapi.testclient import TestClient

from conftest import FakeResponse, FakeSession, ok, png
from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.epub_builder import build, fetch_posts
from phoenix_ebook.errors import PhoenixError
from phoenix_ebook.models import BookSpec, Post, Progress, SourceSpec
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.processors.base import get_processor
from phoenix_ebook.secrets import env_var_name
from web import main, problems, runs, storage
from web.forms import SITE_URL, RawForm
from web.main import app

ADMIN_KEY = "0123456789abcdef01234567:" + "ab" * 32
IMG = "https://img.test"


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv(env_var_name("ghost", "flaminghydra.ghost.io"), ADMIN_KEY)
    monkeypatch.delenv(storage.BLOB_TOKEN_ENV, raising=False)
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path / "books")


def post_route(slug, html="<p>x</p>", title=None):
    body = {"posts": [{"slug": slug, "title": title or slug.title(), "authors": [], "html": html}]}
    return {f"{SITE_URL}/ghost/api/admin/posts/slug/{slug}/": [
        FakeResponse(200, json.dumps(body).encode(), "application/json")]}


def wire(monkeypatch, routes):
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(routes))


def stream(client, data):
    """POST like the page's script does; the parsed lines."""
    r = client.post("/", data=data, headers={"Accept": main.STREAM_TYPE})
    assert r.headers["content-type"].startswith(main.STREAM_TYPE)
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


# ---------------------------------------------------------------- progress from the library

def test_fetch_posts_reports_each_post():
    session = FakeSession({**post_route("a", title="First"), **post_route("b", title="Second")})
    steps = []
    fetch_posts(SourceSpec("ghost", SITE_URL, "generic", ["a", "b"]), GhostPlatform(), ADMIN_KEY, session,
                steps.append)
    assert steps == [Progress("fetch", 1, 2, "First"), Progress("fetch", 2, 2, "Second")]


def test_build_reports_each_chapter_then_writing(tmp_path):
    steps = []
    posts = [Post(slug="a", title="A", html="<p>a</p>"), Post(slug="b", title="B", html="<p>b</p>")]
    build(BookSpec(title="t", output=str(tmp_path / "b.epub")), posts, get_processor("generic"),
          session=FakeSession(), progress=steps.append)
    assert steps == [Progress("post", 1, 2, "A"), Progress("post", 2, 2, "B"), Progress("write", 1, 1)]


@pytest.mark.parametrize("step, expected", [
    (Progress("fetch", 3, 12, "Fender Bender"), "Fetching post 3 of 12: Fender Bender"),
    (Progress("post", 1, 2, None), "Downloading images for post 1 of 2"),
    (Progress("write", 1, 1), "Putting the book together"),
    (Progress("save", 1, 1), "Saving the book"),
])
def test_progress_lines(step, expected):
    assert runs.describe(step) == expected


# ---------------------------------------------------------------- streaming

def test_stream_sends_progress_then_the_results_page(monkeypatch):
    wire(monkeypatch, {**post_route("a"), **post_route("b")})
    lines = stream(TestClient(app), {"posts": "a\nb", "title": "Streamed"})
    progress = [line["message"] for line in lines if line["type"] == "progress"]
    assert progress == ["Fetching post 1 of 2: A", "Fetching post 2 of 2: B",
                        "Downloading images for post 1 of 2: A", "Downloading images for post 2 of 2: B",
                        "Putting the book together", "Saving the book"]
    assert lines[-1]["type"] == "page"
    assert "Your book is ready" in lines[-1]["html"]
    assert "Streamed" in lines[-1]["html"]


def test_screen_readers_hear_milestones_not_every_post():
    announced = [n for n in range(1, 41) if main._announce(Progress("post", n, 40))]
    assert announced == [1, 10, 20, 30, 40]


def test_stream_with_form_errors_ends_on_the_refilled_form(monkeypatch):
    lines = stream(TestClient(app), {"posts": "https://example.com/x", "title": "Kept"})
    assert [line["type"] for line in lines] == ["page"]
    assert 'id="error-summary"' in lines[0]["html"]
    assert 'value="Kept"' in lines[0]["html"]


def test_closing_the_tab_doesnt_stop_the_build(monkeypatch, tmp_path):
    """The stream's reader going away (a closed tab) leaves the build running to the end."""
    wire(monkeypatch, {**post_route("a"), **post_route("b")})
    events = main._stream(RawForm(posts="a\nb", title="Abandoned"))
    assert json.loads(next(events))["type"] == "progress"
    events.close()
    deadline = time.monotonic() + 10
    while not list((tmp_path / "books").glob("*/Abandoned.epub")) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert list((tmp_path / "books").glob("*/Abandoned.epub"))


# ---------------------------------------------------------------- results page

def test_results_page_groups_warnings_by_post_and_explains_them(monkeypatch):
    html_a = f'<p>a</p><img src="{IMG}/gone.png" alt="A view"><img src="{IMG}/ok.png">'
    routes = {**post_route("a", html_a, "Post A"), **post_route("b", "<p>fine</p>", "Post B"),
              f"{IMG}/gone.png": [FakeResponse(404)], f"{IMG}/ok.png": [ok(png())]}
    wire(monkeypatch, routes)
    page = TestClient(app).post("/", data={"posts": "a\nb", "title": "Warned",
                                           "alt_text": "https://img.test/elsewhere.png = x"}).text

    assert page.index("Download “Warned”") < page.index("What the build changed")
    for label in ("Image left out", "No alt text", "Alt-text entry not used"):
        assert f"{label}</a>: 1" in page
    post_a = page.index("<h3 id=\"post-1\">Post A</h3>")
    whole_book = page.index("Whole book</h3>")
    assert post_a < page.index(f"{IMG}/gone.png") < whole_book
    assert "Post B</h3>" not in page  # no warnings, no section
    assert problems.kind("image-missing-alt").meaning in page
    assert f'href="{problems.GUIDE_URL}#image-download-failed"' in page


def test_results_page_without_warnings(monkeypatch):
    wire(monkeypatch, post_route("a"))
    page = TestClient(app).post("/", data={"posts": "a"}).text
    assert "the build had no warnings" in page


def test_every_warning_kind_has_a_label_and_an_explanation():
    from test_troubleshooting import _problem_kinds
    for code in _problem_kinds():
        k = problems.kind(code)
        assert k.label != code, f"add a label for {code} to web/problems.py"
        assert len(k.meaning) > 40, code
        assert "command line" not in k.meaning.lower(), code


# ---------------------------------------------------------------- failures part-way

def test_site_down_while_fetching_returns_to_the_form(monkeypatch):
    routes = {**post_route("a"),
              f"{SITE_URL}/ghost/api/admin/posts/slug/b/": [requests.ConnectionError("down")]}
    wire(monkeypatch, routes)
    page = TestClient(app).post("/", data={"posts": "a\nb", "title": "Kept"}).text
    assert 'id="error-summary"' in page
    assert "can&#39;t reach" in page or "can't reach" in page
    assert 'value="Kept"' in page and "a\nb</textarea>" in page


def test_failure_during_the_build_returns_to_the_form(monkeypatch):
    wire(monkeypatch, post_route("a"))

    def broken_build(*args, **kwargs):
        raise PhoenixError("the disk is full")
    monkeypatch.setattr(runs, "build", broken_build)
    page = TestClient(app).post("/", data={"posts": "a", "title": "Kept"}).text
    assert "the disk is full" in page and 'value="Kept"' in page


def test_a_bug_is_reported_as_a_bug(monkeypatch):
    wire(monkeypatch, post_route("a"))
    monkeypatch.setattr(runs, "build", lambda *a, **k: 1 / 0)
    page = TestClient(app).post("/", data={"posts": "a"}).text
    assert "bug in phoenix-ebook" in page


def test_storage_failure_is_reported(monkeypatch):
    wire(monkeypatch, post_route("a"))

    def refuse(path, title, folder):
        raise OSError("no space left")
    monkeypatch.setattr(runs, "save_book", refuse)
    page = TestClient(app).post("/", data={"posts": "a"}).text
    assert "built but couldn" in page and "no space left" in page


# ---------------------------------------------------------------- storage

def test_local_books_are_served_by_the_site(monkeypatch):
    wire(monkeypatch, post_route("a"))
    client = TestClient(app)
    page = client.post("/", data={"posts": "a", "title": "Fender: Bender"}).text
    url = page.split('class="download" href="')[1].split('"')[0]
    assert url.startswith("/books/") and url.endswith("/Fender-Bender.epub")
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == storage.EPUB_TYPE
    assert r.content[:2] == b"PK"


@pytest.mark.parametrize("key", ["../secret/x.epub", "nope/missing.epub"])
def test_local_book_route_serves_nothing_else(key):
    assert storage.local_book(key) is None


def test_blob_upload_when_deployed(monkeypatch, tmp_path):
    import vercel.blob
    uploads = []

    class Result:
        download_url = "https://store.public.blob.vercel-storage.com/books/x/T.epub?download=1"

    def fake_upload(local_path, pathname, **kwargs):
        uploads.append((pathname, kwargs, open(local_path, "rb").read(2)))
        return Result()
    monkeypatch.setattr(vercel.blob, "upload_file", fake_upload)
    monkeypatch.setenv(storage.BLOB_TOKEN_ENV, "vercel_blob_rw_test")
    epub = tmp_path / "b.epub"
    epub.write_bytes(b"PK...")

    assert storage.save_book(str(epub), "T", storage.new_folder()) == Result.download_url
    (pathname, kwargs, head), = uploads
    assert pathname.startswith("books/") and pathname.endswith("/T.epub") and len(pathname.split("/")[1]) >= 20
    assert kwargs == {"access": "public", "content_type": storage.EPUB_TYPE, "multipart": True}
    assert head == b"PK"
    assert not (tmp_path / "books").exists()
