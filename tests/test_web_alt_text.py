"""Fixing alt text from the results page and rebuilding (#25)."""
from __future__ import annotations

import io
import json
import re

import pytest
import requests

from conftest import FakeResponse, FakeSession, ok, png, web_client
from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.secrets import env_var_name
from web import forms, runs, storage
from web.forms import AltFix, merge_alt_fixes

SITE_URL = "https://flaminghydra.ghost.io"

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
    return client.post("/", data={"posts": "flaminghydra.com/p", "title": "Alt", **extra}).text


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
    page = first_build(web_client())
    by_url = {url: current for url, current in boxes(page).values()}
    assert by_url == {PHOTO: "", LINKED: "", WORDY: "", f"{IMAGES}/named.png": "IMG_5799.jpg"}
    assert page.count("Decorative image</label>") == 3  # not for the image that is the whole link
    linked_box = page[page.index(f'value="{LINKED}"') - 900:page.index(f'value="{LINKED}"')]
    assert "This image is a link: describe where it goes" in linked_box


def test_boxes_are_labelled_with_post_and_position_and_have_a_thumbnail():
    page = first_build(web_client())
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
    client = web_client()
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
    client = web_client()
    page = first_build(client, alt_text="https://elsewhere.example/gone.png = Gone")
    rebuilt = client.post("/", data=rebuild_data(page, {PHOTO: "A red rectangle"})).text
    assert built_specs[-1].alt_text["https://elsewhere.example/gone.png"] == "Gone"
    assert "Alt-text entry not used</a>: 1" in rebuilt


def test_rebuild_keeps_the_cover(built_specs):
    client = web_client()
    page = client.post("/", data={"posts": "flaminghydra.com/p", "title": "Covered"},
                       files={"cover": ("c.png", io.BytesIO(png("blue")), "image/png")}).text
    ref = re.search(r'name="kept_cover" value="([^"]+)"', page)[1]
    assert ref.startswith("/books/") and ref.endswith("/uploads/cover/c.png")

    rebuilt = client.post("/", data=rebuild_data(page, {})).text
    assert "Your book is ready" in rebuilt
    assert built_specs[-1].cover is not None
    assert re.search(r'name="kept_cover" value="([^"]+)"', rebuilt)[1] != ref  # kept again, with the new book

    data = rebuild_data(page, {})
    del data["kept_cover"]  # "Keep the cover" unticked
    client.post("/", data=data)
    assert built_specs[-1].cover is None


def test_a_missing_kept_cover_is_a_form_error():
    page = web_client().post("/", data={"posts": "flaminghydra.com/p", "kept_cover": "/books/nope/uploads/cover/c.png"}).text
    assert "Cover image: the file from the last build couldn" in page and 'id="error-summary"' in page


@pytest.mark.parametrize("ref", [
    "https://evil.example/books/x/uploads/cover.png",
    "https://store.public.blob.vercel-storage.com/other/x.png",
    "file:///etc/passwd",
    "/books/../../etc/passwd",
])
def test_kept_uploads_come_only_from_our_storage(ref, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("fetched " + ref))
    assert storage.load_upload(ref) is None


# ---------------------------------------------------------------- many boxes (#38)

def built_with_images(monkeypatch, counts):
    """A website build of posts p0, p1, … with the given number of images missing alt text, plus a warning."""
    routes = {}
    for i, count in enumerate(counts):
        html = "".join(f'<img src="{IMAGES}/{i}-{n}.png">' for n in range(count)) + '<a href="https://x.example/"></a>'
        body = {"posts": [{"slug": f"p{i}", "title": f"Post {i}", "authors": [], "html": html}]}
        routes[f"{SITE_URL}/ghost/api/admin/posts/slug/p{i}/"] = [FakeResponse(200, json.dumps(body).encode(),
                                                                              "application/json")]
        for n in range(count):
            routes[f"{IMAGES}/{i}-{n}.png"] = [ok(png())]
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(routes))
    posts = "\n".join(f"flaminghydra.com/p{i}" for i in range(len(counts)))
    return web_client().post("/", data={"posts": posts, "title": "Many"}).text


def test_alt_boxes_stay_open_up_to_the_limit(monkeypatch):
    page = built_with_images(monkeypatch, [forms.MAX_OPEN_ALT_BOXES])
    assert '<details class="alt-fixes">' not in page
    assert len(boxes(page)) == forms.MAX_OPEN_ALT_BOXES


def test_alt_boxes_collapse_per_post_over_the_limit_and_other_warnings_stay_open(monkeypatch):
    page = built_with_images(monkeypatch, [forms.MAX_OPEN_ALT_BOXES, 1])
    assert page.count('<details class="alt-fixes">') == 2
    assert f"{forms.MAX_OPEN_ALT_BOXES} images need alt text" in page
    assert "1 image needs alt text" in page
    assert len(boxes(page)) == forms.MAX_OPEN_ALT_BOXES + 1  # every image can still be fixed
    outside = re.sub(r'<details class="alt-fixes">.*?</details>', "", page, flags=re.S)
    assert "fix_url_" not in outside
    assert "https://x.example/" in outside  # the link warning isn't folded away


def test_rebuild_with_hundreds_of_boxes_uses_the_text(monkeypatch, built_specs):
    data = {"posts": "flaminghydra.com/p", "title": "Alt"}
    for n in range(450):
        data |= {f"fix_url_{n}": f"{IMAGES}/{n}.png", f"fix_current_{n}": "", f"fix_alt_{n}": f"Picture {n}"}
    # A browser sends this form as multipart, the kind with a limit on its number of fields.
    response = web_client().post("/", data=data, files={"cover": ("", b"", "application/octet-stream")})
    assert response.status_code == 200
    assert built_specs[-1].alt_text[f"{IMAGES}/449.png"] == "Picture 449"
