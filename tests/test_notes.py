"""Ghost Markdown-card footnotes become canonical notes (#18, docs/canonical-html.md)."""
from __future__ import annotations

import re

import pytest
from bs4 import BeautifulSoup

from conftest import REPO, assert_valid_epub, post
from phoenix_ebook.platforms.ghost import GhostPlatform

MARKDOWN_IT = (
    '<p>Supported the new order.<sup class="footnote-ref"><a href="#fn1" id="fnref1">[1]</a></sup> '
    'And more.<sup class="footnote-ref"><a href="#fn2" id="fnref2">[2]</a></sup></p>'
    '<hr class="footnotes-sep">'
    '<section class="footnotes"><ol class="footnotes-list">'
    '<li id="fn1" class="footnote-item"><p>John Merriman, <em>A History of Modern Europe</em>, 746. '
    '<a href="#fnref1" class="footnote-backref">↩︎</a></p></li>'
    '<li id="fn2" class="footnote-item"><p>Dulffer 111. <a href="https://x/">source</a> '
    '<a href="#fnref2" class="footnote-backref">↩︎</a></p></li>'
    '</ol></section>'
)


def normalized(html: str) -> BeautifulSoup:
    return BeautifulSoup(GhostPlatform().normalize_html(html), "html.parser")


def test_markers_become_noterefs_without_sup_or_brackets():
    soup = normalized(MARKDOWN_IT)
    refs = soup.find_all("a", attrs={"epub:type": "noteref"})
    assert [(a["href"], a["id"], a["role"], a.get_text()) for a in refs] == [
        ("#fn1", "fnref1", "doc-noteref", "1"), ("#fn2", "fnref2", "doc-noteref", "2")]
    assert not soup.find("sup")


def test_notes_section_and_items():
    soup = normalized(MARKDOWN_IT)
    assert not soup.find("hr")
    section = soup.find("section")
    assert section.attrs == {"epub:type": "endnotes", "role": "doc-endnotes"}
    assert section.contents[0].name == "h3" and section.contents[0].get_text() == "Notes"
    items = section.find_all("li")
    assert [(li["id"], li["epub:type"]) for li in items] == [("fn1", "endnote"), ("fn2", "endnote")]
    assert not any(li.has_attr("role") for li in items)  # doc-endnote is deprecated in DPUB-ARIA
    assert not section.find("ol").has_attr("class")
    backlinks = section.find_all("a", attrs={"role": "doc-backlink"})
    assert [a["href"] for a in backlinks] == ["#fnref1", "#fnref2"] and all(not a.has_attr("class") for a in backlinks)


def test_text_and_links_inside_notes_unchanged():
    soup = normalized(MARKDOWN_IT)
    assert "John Merriman, A History of Modern Europe, 746." in soup.find(id="fn1").get_text()
    assert soup.find("a", href="https://x/").get_text() == "source"
    body_text = soup.find("p").get_text()
    assert body_text == "Supported the new order.1 And more.2"


@pytest.mark.parametrize("html", [
    "<p>E = mc<sup>2</sup></p>",                                                     # a lone superscript
    '<p><a href="#notes-on-notes"><strong>Notes on Notes</strong></a></p>',          # an in-page section link
    '<p>x<sup class="footnote-ref"><a href="#fn9" id="fnref9">[9]</a></sup></p>',    # marker with no notes section
])
def test_non_matching_markup_unchanged(html):
    assert GhostPlatform().normalize_html(html) == html


def test_marker_whose_note_is_missing_is_left_alone():
    html = MARKDOWN_IT.replace('href="#fn2" id="fnref2">[2]', 'href="#fn7" id="fnref7">[7]')
    soup = normalized(html)
    orphan = soup.find("a", href="#fn7")
    assert orphan.get_text() == "[7]" and not orphan.has_attr("epub:type") and orphan.parent.name == "sup"


def test_tap_target_rule_moves_nothing_and_sets_no_font():
    css = re.sub(r"/\*.*?\*/", "", (REPO / "phoenix_ebook/styles/phoenix.css").read_text(), flags=re.S)
    rule = re.search(r'a\[epub\|type~="noteref"\]\{(.*?)\}', css, re.S).group(1)
    assert "padding: 0.35em" in rule and "margin: 0 -0.35em" in rule and "font" not in rule


def test_book_with_notes_is_valid(build_book):
    book = build_book(post(GhostPlatform().normalize_html(MARKDOWN_IT)))
    chapter = book.chapter()
    assert 'epub:type="noteref"' in chapter and 'epub:type="endnotes"' in chapter
    assert_valid_epub(book.path)
