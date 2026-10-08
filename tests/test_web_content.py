"""Content-page uploads on the website (#26)."""
from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
import requests
from fastapi.testclient import TestClient

from conftest import REPO, FakeResponse, FakeSession
from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.secrets import env_var_name
from web import runs, storage
from web.forms import SITE_URL
from web.main import app

FIXTURES = REPO / "tests" / "fixtures"
ADMIN_KEY = "0123456789abcdef01234567:" + "ab" * 32
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv(env_var_name("ghost", "flaminghydra.ghost.io"), ADMIN_KEY)
    monkeypatch.delenv(storage.BLOB_TOKEN_ENV, raising=False)
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path / "books")
    body = {"posts": [{"slug": "p", "title": "The Post", "authors": [], "html": "<p>Hi.</p>"}]}
    routes = {f"{SITE_URL}/ghost/api/admin/posts/slug/p/": [
        FakeResponse(200, json.dumps(body).encode(), "application/json")]}
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(routes))


@pytest.fixture
def built_specs(monkeypatch):
    specs = []
    real_build = runs.build

    def spying_build(spec, *args, **kwargs):
        specs.append(spec)
        return real_build(spec, *args, **kwargs)
    monkeypatch.setattr(runs, "build", spying_build)
    return specs


def fixture(name, content_type):
    return (name, io.BytesIO((FIXTURES / name).read_bytes()), content_type)


def book_page(client, page, name):
    url = page.split('class="download" href="')[1].split('"')[0]
    return zipfile.ZipFile(io.BytesIO(client.get(url).content)).read(f"EPUB/{name}").decode()


def kept(page):
    return dict(re.findall(r'name="kept_([a-z_]+)" value="([^"]+)"', page))


def test_word_and_markdown_pages_reach_the_book():
    client = TestClient(app)
    page = client.post("/", data={"posts": "p"}, files={
        "foreword_file": fixture("foreword.docx", DOCX), "notes_file": fixture("notes.md", "text/markdown")}).text
    assert "Your book is ready" in page
    foreword = book_page(client, page, "foreword.xhtml")
    assert "<h2>Foreword</h2>" in foreword and "Arts &amp; Letters &lt;club&gt;" in foreword
    assert "<h3>Sources</h3>" in book_page(client, page, "notes.xhtml")
    # the Word file's picture was left out, and the results page says so
    assert "Images left out of a content page</a>: 1" in page
    assert "1 image in the Foreword file (foreword.docx) left out" in page


def test_unsupported_formats_are_rejected_before_building(built_specs):
    page = TestClient(app).post("/", data={"posts": "p"}, files={
        "foreword_file": ("foreword.pdf", io.BytesIO(b"%PDF-1.7"), "application/pdf")}).text
    assert 'id="error-summary"' in page and "<details open>" in page
    assert "Foreword: foreword.pdf: .pdf files can" in page
    assert re.search(r'id="foreword_file-errors">\s*<p class="field-error">Foreword: foreword.pdf', page)
    assert built_specs == []


def test_a_bad_word_file_is_named_as_uploaded(monkeypatch):
    page = TestClient(app).post("/", data={"posts": "p"}, files={
        "foreword_file": ("Old Foreword.docx", io.BytesIO(b"\xd0\xcf\x11\xe0 old .doc"), DOCX)}).text
    assert "Foreword: Old Foreword.docx isn" in page and "a Word file (.docx)" in page
    assert "/tmp" not in page


def test_uploads_are_kept_for_the_rebuild(built_specs):
    client = TestClient(app)
    page = client.post("/", data={"posts": "p"}, files={"notes_file": fixture("notes.md", "text/markdown")}).text
    refs = kept(page)
    assert list(refs) == ["notes_file"] and refs["notes_file"].endswith("/uploads/notes_file/notes.md")
    assert "Keep notes.md from last time" in page

    rebuilt = client.post("/", data={"posts": "p", "kept_notes_file": refs["notes_file"]}).text
    assert built_specs[-1].notes_file and "<h3>Sources</h3>" in book_page(client, rebuilt, "notes.xhtml")


def test_good_uploads_survive_a_form_error():
    """A refilled form offers the uploads that were fine, so they needn't be chosen again."""
    page = TestClient(app).post("/", data={"posts": "p", "isbn": "bad"}, files={
        "notes_file": fixture("notes.md", "text/markdown"),
        "foreword_file": ("foreword.pdf", io.BytesIO(b"%PDF"), "application/pdf")}).text
    assert 'id="error-summary"' in page
    assert list(kept(page)) == ["notes_file"]


def test_a_new_upload_replaces_the_kept_one(built_specs, tmp_path):
    client = TestClient(app)
    first = client.post("/", data={"posts": "p"}, files={"intro_file": ("intro.txt", io.BytesIO(b"Old"), "text/plain")})
    ref = kept(first.text)["intro_file"]
    page = client.post("/", data={"posts": "p", "kept_intro_file": ref},
                       files={"intro_file": ("intro.txt", io.BytesIO(b"New & improved"), "text/plain")}).text
    assert "New &amp; improved" in book_page(client, page, "intro.xhtml")


def test_hints_say_which_pages_get_a_placeholder():
    html = TestClient(app).get("/").text
    hints = dict(re.findall(r'id="([a-z_]+)-hint">([^<]+)</p>', html))
    for name in ("copyright_file", "imprint_file", "foreword_file", "notes_file", "acknowledgements_file",
                 "about_file"):
        assert "a placeholder page goes in its place" in hints[name], name
    assert "Left out if you don’t upload one" in hints["intro_file"]
    assert "placeholder" not in hints["intro_file"]
