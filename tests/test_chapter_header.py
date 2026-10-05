"""Chapter headers: date, title, byline; plain-title navigation (#4)."""
from __future__ import annotations

import re

import pytest

from conftest import FakeResponse, ok, png, post
from phoenix_ebook.epub_builder import _byline, _format_date
from phoenix_ebook.models import Author

U = "https://img.test/"


@pytest.mark.parametrize("authors, expected", [
    ([Author("A", "https://x/a/")], 'By <a href="https://x/a/">A</a>'),
    ([Author("A"), Author("B")], "By A and B"),
    ([Author("A"), Author("B"), Author("C")], "By A, B and C"),
    ([], None),
])
def test_byline(authors, expected):
    assert _byline(authors) == expected


def test_byline_escapes_names_and_urls():
    assert _byline([Author("Tom & <Jerry>", 'https://x/?a=1&b="2"')]) == \
        'By <a href="https://x/?a=1&amp;b=&quot;2&quot;">Tom &amp; &lt;Jerry&gt;</a>'


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-08-24T20:04:00-04:00", "24 Aug 2026"),  # local time, no shift to UTC
    ("2026-08-10T23:51:51.000Z", "10 Aug 2026"),
    ("2026-01-05T00:00:00+00:00", "05 Jan 2026"),
    (None, None),
    ("garbage", None),
])
def test_format_date(timestamp, expected):
    assert _format_date(timestamp) == expected


def test_header_structure_and_order(build_book):
    p = post("<p>Body.</p>", authors=[Author("A", "https://x/a/"), Author("B")],
             published_at="2026-08-24T20:04:00-04:00", feature_image=U + "f.png", feature_image_caption="Cap")
    chapter = build_book(p, {U + "f.png": [ok(png())]}).chapter()
    body = chapter.split("<body>")[1]
    assert re.search(r'<article id="article-1">\s*<header>', body)
    assert re.search(r"</article>\s*</body>\s*</html>\s*$", body)  # article wraps everything
    header = re.search(r"<header>.*?</header>", body, re.S).group(0)
    order = re.findall(r'class="date"|<h2>|class="byline"|<figure>', header)
    assert order == ['class="date"', "<h2>", 'class="byline"', "<figure>"]
    assert '<p class="date">24 Aug 2026</p>' in header
    assert "data-feature-image" not in chapter


def test_title_is_escaped_and_nav_uses_plain_title(build_book):
    book = build_book(post(title='Cats & <Dogs> "q"'))
    assert '<h2>Cats &amp; &lt;Dogs&gt; "q"</h2>' in book.chapter()
    nav = book.text("nav.xhtml")
    assert "Cats &amp; &lt;Dogs&gt;" in nav and " — " not in nav


def test_bare_post_header_is_just_the_title(build_book):
    header = re.search(r"<header>.*?</header>", build_book(post(title="Bare")).chapter(), re.S).group(0)
    assert re.sub(r"\s", "", header) == "<header><h2>Bare</h2></header>"


def test_chapters_are_numbered(build_book):
    book = build_book([post(slug="one"), post(slug="two")])
    assert '<article id="article-2">' in book.chapter("two")


def test_failed_feature_image_leaves_header_without_figure(build_book):
    book = build_book(post(feature_image=U + "gone.png"), {U + "gone.png": [FakeResponse(404)]})
    chapter = book.chapter()
    assert "<header>" in chapter and "<figure>" not in chapter
