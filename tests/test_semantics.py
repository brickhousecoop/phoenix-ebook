"""Section roles and no-break glue before em dashes / ellipses (#9)."""
from __future__ import annotations

import re

import pytest
from bs4 import BeautifulSoup

from conftest import assert_valid_epub, ok, png, post
from phoenix_ebook.epub_builder import glue

FEFF = "﻿"
U = "https://img.test/"


def body_role(page: str) -> str | None:
    m = re.search(r'<body(?: epub:type="([^"]*)")?>', page)
    return m.group(1) if m else None


def section_role(page: str) -> str | None:
    m = re.search(r'<(?:section|article)[^>]* epub:type="([^"]*)"', page)
    return m.group(1) if m else None


@pytest.fixture
def full_book(build_book, tmp_path):
    intro = tmp_path / "intro.txt"
    intro.write_text("An intro paragraph.")
    return build_book([post(slug="one", title="One"), post(slug="two", title="Two")], intro_file=str(intro))


@pytest.mark.parametrize("page, part, specific", [
    ("titlepage.xhtml", "frontmatter", "titlepage"),
    ("copyright.xhtml", "frontmatter", "copyright-page"),
    ("imprint.xhtml", "frontmatter", "imprint"),
    ("foreword.xhtml", "frontmatter", "foreword"),
    ("intro.xhtml", "frontmatter", "introduction"),
    ("halftitlepage.xhtml", "frontmatter", "halftitlepage"),
    ("one.xhtml", "bodymatter", "chapter"),
    ("notes.xhtml", "backmatter", None),
    ("acknowledgements.xhtml", "backmatter", "acknowledgments"),
    ("about.xhtml", "backmatter", None),
])
def test_page_roles(full_book, page, part, specific):
    html = full_book.text(page)
    assert body_role(html) == part
    assert section_role(html) == specific


def test_contents_page_is_front_matter(full_book):
    nav = full_book.text("nav.xhtml")
    assert body_role(nav) == "frontmatter" and '<nav epub:type="toc"' in nav


def test_content_file_is_wrapped_not_modified(build_book, tmp_path):
    foreword = tmp_path / "foreword.html"
    foreword.write_text('<h2>Foreword</h2><p class="mine">A — dash… and <em>more</em>.</p>')
    page = build_book(post(), foreword_file=str(foreword)).text("foreword.xhtml")
    section = BeautifulSoup(page, "html.parser").find("section")
    assert section["epub:type"] == "foreword"
    assert str(section.find("p")) == '<p class="mine">A — dash… and <em>more</em>.</p>'  # no glue, unchanged


# ---------------------------------------------------------------- glue

@pytest.mark.parametrize("text, expected", [
    ("word—word", f"word{FEFF}—word"),
    ("wait…", f"wait{FEFF}…"),
    ("a — b … c", f"a {FEFF}— b {FEFF}… c"),
    (f"already{FEFF}—glued", f"already{FEFF}—glued"),
    ("joined⁠—word", "joined⁠—word"),
    ("nbsp —dash", "nbsp —dash"),
    ("en dash – stays", "en dash – stays"),
])
def test_glue(text, expected):
    assert glue(text) == expected


def test_glue_is_idempotent():
    once = glue("a—b…c—d")
    assert glue(once) == once


def test_chapter_text_and_feature_caption_are_glued(build_book):
    html = ('<p>Before—after… end.</p><pre>code—kept…</pre><p><code>x—y</code></p>'
            '<p><a href="https://x/a—b" title="t—t">link—text</a></p>')
    p = post(html, title="Title—With Dash", feature_image=U + "f.png", feature_image_caption="Caption—here")
    chapter = build_book(p, {U + "f.png": [ok(png())]}).chapter()
    assert f"Before{FEFF}—after{FEFF}… end." in chapter
    assert f"Caption{FEFF}—here" in chapter
    assert f"Title{FEFF}—With Dash" in chapter
    assert f"link{FEFF}—text" in chapter
    assert "code—kept…" in chapter and "<code>x—y</code>" in chapter          # code untouched
    assert 'href="https://x/a—b"' in chapter and 'title="t—t"' in chapter     # attributes untouched


def test_generated_pages_are_glued(build_book):
    book = build_book(post(), title="Book—Title", subtitle="Sub…")
    assert f"Book{FEFF}—Title" in book.text("titlepage.xhtml")
    assert f"Sub{FEFF}…" in book.text("halftitlepage.xhtml")
    assert f"placeholder {FEFF}— replace" in book.text("copyright.xhtml")


def test_visible_text_otherwise_unchanged(build_book):
    html = "<p>One—two… three — four.</p><blockquote><p>Q—uote</p></blockquote>"
    chapter = build_book(post(html)).chapter()
    text = BeautifulSoup(chapter, "html.parser").find("article").get_text().replace(FEFF, "")
    assert "One—two… three — four." in text and "Q—uote" in text


def test_full_book_with_roles_and_glue_is_valid(full_book):
    assert_valid_epub(full_book.path)
