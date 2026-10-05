"""Post feature images (#2)."""
from __future__ import annotations

import re

from conftest import FakeResponse, ok, png, post
from phoenix_ebook.models import Post

U = "https://img.test/"
BODY = f'<p>Body text.</p><figure><img src="{U}body.png" alt="b"/></figure>'
ROUTES = {U + "feat.png": [ok(png("blue"))], U + "body.png": [ok(png())]}


def header(chapter: str) -> str:
    return re.search(r"<header>.*?</header>", chapter, re.S).group(0)


def test_feature_image_with_alt_and_caption_opens_the_chapter(build_book):
    p = post(BODY, feature_image=U + "feat.png", feature_image_alt="An angel",
             feature_image_caption="<b>Klee</b>, via <a href='https://w/'>Wiki</a>")
    book = build_book(p, ROUTES)
    figure = re.search(r"<figure>.*?</figure>", header(book.chapter()), re.S).group(0)
    assert 'alt="An angel"' in figure
    assert '<figcaption><b>Klee</b>, via <a href="https://w/">Wiki</a></figcaption>' in figure
    src = re.search(r'src="([^"]+)"', figure).group(1)
    assert src.startswith("images/") and src in book.text("content.opf")
    assert not book.result.problems


def test_missing_caption_and_alt(build_book):
    figure = header(build_book(post(BODY, feature_image=U + "feat.png"), ROUTES).chapter())
    assert "figcaption" not in figure and 'alt=""' in figure


def test_post_without_feature_image_has_only_body_figures(build_book):
    chapter = build_book(post(BODY), ROUTES).chapter()
    assert chapter.count("<figure>") == 1 and "<figure>" not in header(chapter)


def test_feature_image_already_in_body_is_not_duplicated(build_book):
    book = build_book(post(BODY, feature_image=U + "body.png"), ROUTES)
    assert book.chapter().count("<img") == 1 and book.session.calls[U + "body.png"] == 1


def test_duplicate_detected_via_relative_url(build_book):
    site = "https://site.test"
    p = Post(slug="p", title="T", html='<img src="/content/images/x.png"/>',
             feature_image=f"{site}/content/images/x.png")
    book = build_book(p, {f"{site}/content/images/x.png": [ok(png())]}, image_base_url=site)
    assert book.chapter().count("<img") == 1


def test_failed_feature_image_leaves_no_figure_and_is_reported(build_book):
    p = post(BODY, feature_image=U + "gone.png", feature_image_caption="Lost caption")
    book = build_book(p, {**ROUTES, U + "gone.png": [FakeResponse(404)]})
    chapter = book.chapter()
    assert "<figure>" not in header(chapter) and "Lost caption" not in chapter
    [problem] = book.result.problems
    assert problem.url == U + "gone.png" and "404" in problem.detail
