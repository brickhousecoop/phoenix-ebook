"""Stylesheets: pinned Standard Ebooks core.css, phoenix.css, and user --css (#7)."""
from __future__ import annotations

import hashlib
import re

import pytest

import build_epub
from conftest import REPO, post
from phoenix_ebook.epub_builder import MissingContentFile

STYLES = REPO / "phoenix_ebook" / "styles"

# The upstream commit core.css was copied from, and the sha256 of upstream
# se/data/templates/core.css at that commit. When deliberately taking a new
# version, update the file, its header, and both values together.
CORE_CSS_UPSTREAM_COMMIT = "a6571412ae3845d1d9ac646696bc5b5607a739c6"
CORE_CSS_UPSTREAM_SHA256 = "830a0d4aa8028ad9ca1fc4e69df551dd7a389817df105a92786191d15e68e9a7"


def upstream_bytes_of_bundled_core() -> bytes:
    """Our core.css minus the provenance comment inserted after @charset."""
    text = (STYLES / "core.css").read_text(encoding="utf-8")
    first_line, rest = text.split("\n", 1)
    assert first_line == '@charset "utf-8";'
    header_end = rest.index("*/\n") + len("*/\n")
    assert rest.startswith("/*") and "Standard Ebooks core.css" in rest[:header_end]
    return (first_line + "\n" + rest[header_end:]).encode("utf-8")


def test_core_css_is_the_unmodified_upstream_snapshot():
    digest = hashlib.sha256(upstream_bytes_of_bundled_core()).hexdigest()
    assert digest == CORE_CSS_UPSTREAM_SHA256, "core.css body was edited; put overrides in phoenix.css"


def test_core_css_header_records_source_commit_and_license():
    header = (STYLES / "core.css").read_text(encoding="utf-8").split("*/", 1)[0]
    source = f"github.com/standardebooks/tools/blob/{CORE_CSS_UPSTREAM_COMMIT}/se/data/templates/core.css"
    assert source in header and f"Commit: {CORE_CSS_UPSTREAM_COMMIT}" in header
    assert "CC0" in header


def css_rules(name: str) -> str:
    """The stylesheet with comments removed, so checks see declarations only."""
    return re.sub(r"/\*.*?\*/", "", (STYLES / name).read_text(encoding="utf-8"), flags=re.S)


def test_no_fonts_are_set():
    assert "font-family" not in css_rules("phoenix.css")
    assert "font-family" not in css_rules("core.css")


def test_phoenix_css_keeps_figures_together_and_images_on_screen():
    css = css_rules("phoenix.css")
    figure_rules = re.search(r"\nfigure\{(.*?)\}", css, re.S).group(1)
    assert "break-inside: avoid" in figure_rules
    img_rules = re.search(r"\nfigure img\{(.*?)\}", css, re.S).group(1)
    assert "max-height" in img_rules


def test_bold_is_restored_over_core_small_caps():
    css = css_rules("phoenix.css")
    bold = re.search(r"\nb,\nstrong\{(.*?)\}", css, re.S).group(1)
    assert "font-weight: bold" in bold and "font-variant: normal" in bold


# ---------------------------------------------------------------- in the book

def stylesheet_links(page: str) -> list[str]:
    return re.findall(r'<link href="([^"]+\.css)" rel="stylesheet"', page)


def content_pages(book) -> list[str]:
    opf = book.text("content.opf")
    return re.findall(r'<item href="([^"]+\.xhtml)"', opf)


TWO_POSTS = [post(slug="one", title="One"), post(slug="two", title="Two")]


def test_every_page_links_core_then_phoenix(build_book):
    book = build_book(TWO_POSTS)
    pages = content_pages(book)
    assert {"titlepage.xhtml", "nav.xhtml", "halftitlepage.xhtml", "copyright.xhtml", "one.xhtml"} <= set(pages)
    for page in pages:
        assert stylesheet_links(book.text(page)) == ["style/core.css", "style/phoenix.css"], page
    opf = book.text("content.opf")
    assert 'href="style/core.css"' in opf and 'href="style/phoenix.css"' in opf
    assert book.zip.read("EPUB/style/core.css") == (STYLES / "core.css").read_bytes()


def test_user_css_is_copied_and_linked_last(build_book, tmp_path):
    extra = tmp_path / "extra.css"
    extra.write_text("p { color: #333; }")
    book = build_book(TWO_POSTS, css=str(extra))
    for page in content_pages(book):
        assert stylesheet_links(book.text(page)) == ["style/core.css", "style/phoenix.css", "style/user.css"], page
    assert book.zip.read("EPUB/style/user.css") == b"p { color: #333; }"


def test_missing_user_css_raises(build_book, tmp_path):
    with pytest.raises(MissingContentFile, match="--css"):
        build_book(TWO_POSTS, css=str(tmp_path / "nope.css"))


def test_cli_missing_css_exits_before_fetching(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(build_epub, "get_platform", lambda name: fetched.append(name))
    with pytest.raises(SystemExit) as exit_info:
        build_epub.main(["--css", "nope.css", "--admin-key", "k:00", "--output", str(tmp_path / "b.epub"), "slug"])
    assert "--css" in str(exit_info.value.code) and not fetched


def test_contents_page_is_headed_contents_and_unnumbered(build_book):
    nav = build_book(TWO_POSTS).text("nav.xhtml")
    assert "<title>Contents</title>" in nav and "<h2>Contents</h2>" in nav
    toc_list = re.search(r'nav\[epub\|type~="toc"\] ol\{(.*?)\}', css_rules("phoenix.css"), re.S).group(1)
    assert "list-style-type: none" in toc_list
