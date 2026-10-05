"""Images that can't be fetched as valid images (#1)."""
from __future__ import annotations

import re

import pytest
import requests

from conftest import FakeResponse, FakeSession, SVG, ok, png, post
from phoenix_ebook.images import ImageFetchError, fetch_image, validate_image
from phoenix_ebook.models import BuildResult

U = "https://img.test/"


def remote_img_srcs(html: str) -> list[str]:
    return re.findall(r'<img[^>]+src="(https?://[^"]+)"', html)


# ---------------------------------------------------------------- fetch_image

@pytest.mark.parametrize("first", [
    requests.ConnectionError("blip"),
    requests.Timeout("slow"),
    FakeResponse(503),
    FakeResponse(429),
])
def test_transient_failure_is_retried_once(first):
    session = FakeSession({U: [first, ok(png())]})
    body, _ = fetch_image(session, U)
    assert body == png() and session.calls[U] == 2


@pytest.mark.parametrize("status", [403, 404, 410])
def test_client_error_fails_without_retry(status):
    session = FakeSession({U: [FakeResponse(status)]})
    with pytest.raises(ImageFetchError, match=str(status)):
        fetch_image(session, U)
    assert session.calls[U] == 1


def test_gives_up_after_one_retry():
    session = FakeSession({U: [requests.ConnectionError("down")]})
    with pytest.raises(ImageFetchError, match="ConnectionError"):
        fetch_image(session, U)
    assert session.calls[U] == 2


# ---------------------------------------------------------------- validate_image

def test_validate_accepts_raster_and_keeps_extension():
    assert validate_image(png(), ".png") == ".png"


def test_validate_detects_svg_without_extension():
    assert validate_image(SVG, ".jpg") == ".svg"


@pytest.mark.parametrize("body", [b"<html><body>login</body></html>", b"<html/>", b"\x89PNG garbage", b""])
def test_validate_rejects_non_images(body):
    with pytest.raises(ImageFetchError):
        validate_image(body, ".png")


# ---------------------------------------------------------------- in the book

def test_build_returns_build_result(build_book):
    book = build_book(post())
    assert isinstance(book.result, BuildResult) and book.result.problems == []


def test_failed_image_is_removed_and_reported(build_book):
    book = build_book(post(f'<p>a</p><img src="{U}a.jpg" alt="a"/>'),
                      {U + "a.jpg": [requests.ConnectionError("down")]})
    chapter = book.chapter()
    assert "<img" not in chapter and not remote_img_srcs(chapter)
    [problem] = book.result.problems
    assert (problem.kind, problem.post_slug, problem.url) == ("image-download-failed", "p", U + "a.jpg")


def test_404_is_reported_with_status(build_book):
    book = build_book(post(f'<img src="{U}b.png"/>'), {U + "b.png": [FakeResponse(404, b"nf", "text/html")]})
    assert "<img" not in book.chapter()
    assert "404" in book.result.problems[0].detail


def test_html_body_is_rejected_with_content_type(build_book):
    book = build_book(post(f'<img src="{U}c.jpg"/>'), {U + "c.jpg": [ok(b"<html>login</html>", "text/html")]})
    assert "<img" not in book.chapter()
    assert "text/html" in book.result.problems[0].detail
    assert book.session.calls[U + "c.jpg"] == 1


def test_flaky_image_is_embedded_after_retry(build_book):
    book = build_book(post(f'<img src="{U}d.png"/>'), {U + "d.png": [FakeResponse(503), ok(png())]})
    assert 'src="images/' in book.chapter() and not book.result.problems


def test_figure_left_empty_is_removed_with_its_caption(build_book):
    html = (f'<figure class="one"><img src="{U}bad.png"/><figcaption>CAP1</figcaption></figure>'
            f'<figure class="two"><img src="{U}bad.png"/><img src="{U}good.png"/><figcaption>CAP2</figcaption></figure>')
    book = build_book(post(html), {U + "bad.png": [FakeResponse(404)], U + "good.png": [ok(png("blue"))]})
    chapter = book.chapter()
    assert "CAP1" not in chapter and 'class="one"' not in chapter
    assert "CAP2" in chapter and chapter.count("<img") == 1


def test_link_left_empty_is_removed_but_link_with_text_kept(build_book):
    html = (f'<p><a href="https://x/1"><img src="{U}bad.png"/></a></p>'
            f'<p><a href="https://x/2"><img src="{U}bad.png"/> text</a></p>')
    chapter = build_book(post(html), {U + "bad.png": [FakeResponse(404)]}).chapter()
    assert "https://x/1" not in chapter
    assert "https://x/2" in chapter and "text" in chapter


def test_repeated_failed_url_is_not_refetched_but_reported_each_time(build_book):
    book = build_book(post(f'<img src="{U}e.png"/><p>x</p><img src="{U}e.png"/>'),
                      {U + "e.png": [requests.ConnectionError("down")]})
    assert book.session.calls[U + "e.png"] == 2  # first attempt + one retry, never again
    assert len(book.result.problems) == 2


def test_validation_still_applies_with_optimization_disabled(build_book):
    big = png("green", (3000, 2000))
    book = build_book(post(f'<img src="{U}f.png"/><img src="{U}g.png"/>'),
                      {U + "f.png": [ok(b"\x89PNG garbage")], U + "g.png": [ok(big)]},
                      optimize_images=False)
    [problem] = book.result.problems
    assert problem.url == U + "f.png"
    [name] = book.image_names()
    assert book.zip.read(name) == big  # embedded byte-for-byte


def test_svg_without_extension_is_stored_as_svg(build_book):
    book = build_book(post(f'<img src="{U}logo"/>'), {U + "logo": [ok(SVG, "application/octet-stream")]})
    assert any(n.endswith(".svg") for n in book.image_names()) and not book.result.problems
