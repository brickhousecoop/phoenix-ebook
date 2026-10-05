"""Built books pass epubcheck. Skipped when Java or the epubcheck jar is missing."""
from __future__ import annotations

import requests

from conftest import FakeResponse, assert_valid_epub, jpeg, ok, png, post
from phoenix_ebook.models import Author

U = "https://img.test/"


def test_default_book_with_cover_is_valid(build_book, tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(jpeg(size=(600, 900)))
    p = post(f'<p>Body &amp; more.</p><figure><img src="{U}a.png" alt="a"/><figcaption>c</figcaption></figure>',
             title="Cats & <Dogs>", authors=[Author("A", "https://x/a/")],
             published_at="2026-08-24T20:04:00-04:00", feature_image=U + "f.png", feature_image_alt="f")
    book = build_book(p, {U + "a.png": [ok(png())], U + "f.png": [ok(png("blue"))]},
                      cover=str(cover), author="Ed", publisher="Pub")
    assert_valid_epub(book.path)


def test_book_where_every_image_failed_is_valid(build_book):
    p = post(f'<img src="{U}a.png"/><figure><img src="{U}b.png"/><figcaption>x</figcaption></figure>',
             feature_image=U + "f.png")
    book = build_book(p, {U + "a.png": [requests.ConnectionError("down")],
                          U + "b.png": [FakeResponse(404)], U + "f.png": [ok(b"<html/>", "text/html")]})
    assert len(book.result.problems) == 3
    assert_valid_epub(book.path)


def test_no_placeholders_book_without_cover_is_valid(build_book):
    book = build_book([post(slug="one", title="One"), post(slug="two", title="Two")], placeholders=False)
    assert_valid_epub(book.path)


def test_book_with_user_css_is_valid(build_book, tmp_path):
    extra = tmp_path / "extra.css"
    extra.write_text("p { color: #333; }\nfigcaption { font-style: italic; }\n")
    book = build_book([post(slug="one", title="One")], css=str(extra))
    assert_valid_epub(book.path)
