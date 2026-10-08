"""Fixing alt text from the results page and rebuilding (#25)."""
from __future__ import annotations

import io
import json
import re

import pytest
import requests
from fastapi.testclient import TestClient

from conftest import FakeResponse, FakeSession, ok, png
from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.secrets import env_var_name
from web import forms, runs, storage
from web.forms import AltFix, SITE_URL, merge_alt_fixes
from web.main import app

ADMIN_KEY = "0123456789abcdef01234567:" + "ab" * 32
IMAGES = "https://storage.ghost.io/c/x/content/images/2026/04"
PHOTO, LINKED, WORDY = f"{IMAGES}/photo.png", f"{IMAGES}/linked.png", f"{IMAGES}/wordy.png"
POST_HTML = (f'<p>Hi.</p><img src="{PHOTO}">'
             f'<a href="https://shop.example/book"><img src="{LINKED}"></a>'
             f'<a href="https://example.org/x"><img src="{WORDY}"> and words</a>'
             f'<img src="{IMAGES}/named.png" alt="IMG_5799.jpg">')


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv(env_var_name("ghost", "flaminghydra.ghost.io"), ADMIN_KEY)
    monkeypatch.delenv(storage.BLOB_TOKEN_ENV, raising=False)
    monkeypatch.setattr(storage, "LOCAL_DIR", tmp_path / "books")
    body = {"posts": [{"slug": "p", "title": "The Post", "authors": [], "html": POST_HTML}]}
    routes = {f"{SITE_URL}/ghost/api/admin/posts/slug/p/": [FakeResponse(200, json.dumps(body).encode(),
                                                                       "application/json")]}
    for name in ("photo", "linked", "wordy", "named"):
        routes[f"{IMAGES}/{name}.png"] = [ok(png())]
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(routes))


@pytest.fixture
def built_specs(monkeypatch):
    """Every BookSpec the website builds, in order."""
    specs = []
    real_build = runs.build

    def spying_build(spec, *args, **kwargs):
        specs.append(spec)
        return real_build(spec, *args, **kwargs)
    monkeypatch.setattr(runs, "build", spying_build)
    return specs


def first_build(client, **extra):
    return client.post("/", data={"posts": "p", "title": "Alt", **extra}).text


def boxes(page):
    """fix number -> (url, prefilled alt) for each alt-text box on a results page."""
    urls = dict(re.findall(r'name="fix_url_(\d+)" value="([^"]+)"', page))
    current = dict(re.findall(r'name="fix_current_(\d+)" value="([^"]*)"', page))
    return {n: (url, current[n]) for n, url in urls.items()}


def rebuild_data(page, fixes):
    """The results page's form as a browser would send it, with ``fixes`` (url -> text, or None for decorative)."""
    data = {"posts": re.search(r'name="posts"[^>]*>([^<]*)</textarea>', page)[1],
            "title": re.search(r'name="title" value="([^"]*)"', page)[1],
            "alt_text": re.search(r'name="alt_text"[^>]*>([^<]*)</textarea>', page)[1]}
    for n, (url, current) in boxes(page).items():
        data[f"fix_url_{n}"], data[f"fix_current_{n}"] = url, current
        data[f"fix_alt_{n}"] = current
        if url in fixes:
            if fixes[url] is None:
                data[f"fix_decorative_{n}"] = "on"
            else:
                data[f"fix_alt_{n}"] = fixes[url]
    if m := re.search(r'name="kept_cover" value="([^"]+)"', page):
        data["kept_cover"] = m[1]
    return data


# ---------------------------------------------------------------- what the build records

def test_build_records_current_alt_and_whether_the_image_is_the_link(built_specs):
    page = first_build(TestClient(app))
    by_url = {url: current for url, current in boxes(page).values()}
    assert by_url == {PHOTO: "", LINKED: "", WORDY: "", f"{IMAGES}/named.png": "IMG_5799.jpg"}
    assert page.count("Decorative image</label>") == 3  # not for the image that is the whole link
    linked_box = page[page.index(f'value="{LINKED}"') - 900:page.index(f'value="{LINKED}"')]
    assert "This image is a link: describe where it goes" in linked_box


def test_boxes_are_labelled_with_post_and_position_and_have_a_thumbnail():
    page = first_build(TestClient(app))
    assert '<label for="fix_alt_0">Alt text for image 1 of 4 in “The Post”</label>' in page
    assert f'src="{IMAGES.replace("/content/images/", "/content/images/size/w300/format/webp/")}/photo.png"' in page
    assert re.search(r'<img class="thumb" src="[^"]+" alt="" loading="lazy">', page)


@pytest.mark.parametrize("url, expected", [
    (PHOTO, f"{IMAGES.replace('/content/images/', '/content/images/size/w300/format/webp/')}/photo.png"),
    (f"{IMAGES.replace('/content/images/', '/content/images/size/w1000/')}/photo.png?v=2",
     f"{IMAGES.replace('/content/images/', '/content/images/size/w300/format/webp/')}/photo.png"),
    (f"{IMAGES}/logo.svg", f"{IMAGES}/logo.svg"),
    ("https://elsewhere.example/a.jpg", "https://elsewhere.example/a.jpg"),
])
def test_ghost_thumbnail_url(url, expected):
    assert GhostPlatform().thumbnail_url(url) == expected


# ---------------------------------------------------------------- fixes -> the Alt text field

def test_merge_alt_fixes():
    merged = merge_alt_fixes("old.png = Old", [
        AltFix("a.png", "A  cat\non a mat"),
        AltFix("b.png", decorative=True),
        AltFix("c.png", "IMG_1.jpg", current="IMG_1.jpg"),  # left as prefilled: not a fix
        AltFix("d.png", ""),
    ])
    assert merged == "old.png = Old\na.png = A cat on a mat\nb.png = "
    assert forms._parse_kv_lines(merged) == {"old.png": "Old", "a.png": "A cat on a mat", "b.png": ""}


def test_alt_text_keys_may_contain_equals_signs():
    assert forms._parse_kv_lines("https://x/a.png?v=2 = A view = nice\nName=Last, First") == {
        "https://x/a.png?v=2": "A view = nice", "Name": "Last, First"}


# ---------------------------------------------------------------- the rebuild

def test_rebuild_applies_fixes_and_stops_listing_those_images(built_specs):
    client = TestClient(app)
    page = first_build(client)
    rebuilt = client.post("/", data=rebuild_data(page, {
        PHOTO: "A red rectangle", LINKED: "The book, at the shop", f"{IMAGES}/wordy.png": None})).text

    alt = built_specs[-1].alt_text
    assert alt[PHOTO] == "A red rectangle" and alt[LINKED] == "The book, at the shop" and alt[WORDY] == ""
    assert {url for url, _ in boxes(rebuilt).values()} == {f"{IMAGES}/named.png"}
    assert "No alt text</a>" not in rebuilt
    # the fixes survive the round trip: they're in the Alt text field for the next rebuild
    assert f"{PHOTO} = A red rectangle" in rebuilt and f"{WORDY} = " in rebuilt


def test_rebuild_keeps_earlier_overrides_and_reports_unused_ones(built_specs):
    client = TestClient(app)
    page = first_build(client, alt_text="https://elsewhere.example/gone.png = Gone")
    rebuilt = client.post("/", data=rebuild_data(page, {PHOTO: "A red rectangle"})).text
    assert built_specs[-1].alt_text["https://elsewhere.example/gone.png"] == "Gone"
    assert "Alt-text entry not used</a>: 1" in rebuilt


def test_rebuild_keeps_the_cover(built_specs):
    client = TestClient(app)
    page = client.post("/", data={"posts": "p", "title": "Covered"},
                       files={"cover": ("c.png", io.BytesIO(png("blue")), "image/png")}).text
    ref = re.search(r'name="kept_cover" value="([^"]+)"', page)[1]
    assert ref.startswith("/books/") and "/uploads/cover.png" in ref

    rebuilt = client.post("/", data=rebuild_data(page, {})).text
    assert "Your book is ready" in rebuilt
    assert built_specs[-1].cover is not None
    assert re.search(r'name="kept_cover" value="([^"]+)"', rebuilt)[1] != ref  # kept again, with the new book

    data = rebuild_data(page, {})
    del data["kept_cover"]  # "Keep the cover" unticked
    client.post("/", data=data)
    assert built_specs[-1].cover is None


def test_a_missing_kept_cover_is_a_form_error():
    page = TestClient(app).post("/", data={"posts": "p", "kept_cover": "/books/nope/uploads/cover.png"}).text
    assert "the cover from the last build couldn" in page and 'id="error-summary"' in page


@pytest.mark.parametrize("ref", [
    "https://evil.example/books/x/uploads/cover.png",
    "https://store.public.blob.vercel-storage.com/other/x.png",
    "file:///etc/passwd",
    "/books/../../etc/passwd",
])
def test_kept_uploads_come_only_from_our_storage(ref, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("fetched " + ref))
    assert storage.load_upload(ref) is None
