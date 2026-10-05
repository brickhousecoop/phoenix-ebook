"""Book structure: title/half-title pages, reading order, contents page, placeholders (#6)."""
from __future__ import annotations

import re

import pytest

import build_epub
from conftest import jpeg, post
from phoenix_ebook.epub_builder import MissingContentFile


def reading_order(book) -> list[str]:
    """File names in spine order."""
    opf = book.text("content.opf")
    hrefs = dict(re.findall(r'<item href="([^"]+)" id="([^"]+)"', opf))
    by_id = {item_id: href for href, item_id in hrefs.items()}
    return [by_id[i] for i in re.findall(r'<itemref idref="([^"]+)"', opf)]


def toc_entries(book) -> list[str]:
    toc = re.search(r'<nav epub:type="toc".*?</nav>', book.text("nav.xhtml"), re.S).group(0)
    return re.findall(r'<a href="([^"]+)"', toc)


def landmarks(book) -> dict[str, str]:
    nav = re.search(r'<nav epub:type="landmarks" hidden="hidden">.*?</nav>', book.text("nav.xhtml"), re.S).group(0)
    return dict(re.findall(r'<a epub:type="([^"]+)" href="([^"]+)"', nav))


TWO_POSTS = [post(slug="one", title="One"), post(slug="two", title="Two")]


def test_cover_is_metadata_only(build_book, tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(jpeg(size=(600, 900)))
    book = build_book(TWO_POSTS, cover=str(cover))
    assert not [n for n in book.zip.namelist() if n.endswith(".xhtml") and "cover" in n]
    opf = book.text("content.opf")
    assert re.search(r'<item href="images/cover\.jpg" id="cover-image" [^>]*properties="cover-image"', opf)
    assert '<meta name="cover" content="cover-image"' in opf
    assert reading_order(book)[0] == "titlepage.xhtml"


def test_default_reading_order(build_book):
    assert reading_order(build_book(TWO_POSTS)) == [
        "titlepage.xhtml", "copyright.xhtml", "imprint.xhtml", "nav.xhtml", "foreword.xhtml",
        "halftitlepage.xhtml", "one.xhtml", "two.xhtml",
        "notes.xhtml", "acknowledgements.xhtml", "about.xhtml",
    ]


def test_intro_sits_after_foreword_before_half_title(build_book, tmp_path):
    intro = tmp_path / "intro.txt"
    intro.write_text("Hello.\nSecond paragraph.")
    order = reading_order(build_book(TWO_POSTS, intro_file=str(intro)))
    assert order[order.index("foreword.xhtml") + 1:order.index("one.xhtml")] == ["intro.xhtml", "halftitlepage.xhtml"]


def test_title_page_content(build_book):
    page = build_book(TWO_POSTS, title="Cats & <Dogs>", editor="Ed", publisher="Pub").text("titlepage.xhtml")
    assert "<h1>Cats &amp; &lt;Dogs&gt;</h1>" in page
    assert '<p class="editor">Ed</p>' in page and '<p class="publisher">Pub</p>' in page


def test_title_page_omits_absent_fields(build_book):
    page = build_book(TWO_POSTS, title="Just A Title").text("titlepage.xhtml")
    assert "<h1>Just A Title</h1>" in page and "editor" not in page and "publisher" not in page


def test_no_placeholders_without_files(build_book):
    book = build_book(TWO_POSTS, placeholders=False)
    assert reading_order(book) == ["titlepage.xhtml", "nav.xhtml", "one.xhtml", "two.xhtml"]
    assert "placeholder" not in " ".join(book.text(n) for n in reading_order(book))


def test_no_placeholders_with_only_a_foreword(build_book, tmp_path):
    foreword = tmp_path / "foreword.html"
    foreword.write_text("<h2>Foreword</h2><p>Hi.</p>")
    book = build_book(TWO_POSTS, placeholders=False, foreword_file=str(foreword))
    assert reading_order(book) == ["titlepage.xhtml", "nav.xhtml", "foreword.xhtml",
                                   "halftitlepage.xhtml", "one.xhtml", "two.xhtml"]


def test_supplied_file_is_used_instead_of_placeholder(build_book, tmp_path):
    notes = tmp_path / "notes.html"
    notes.write_text("<h2>Notes</h2><p>Real notes.</p>")
    book = build_book(TWO_POSTS, notes_file=str(notes))
    assert "Real notes." in book.text("notes.xhtml") and "placeholder" not in book.text("notes.xhtml")


def test_contents_page_starts_at_the_foreword(build_book):
    book = build_book(TWO_POSTS)
    entries = toc_entries(book)
    assert entries == ["foreword.xhtml", "one.xhtml", "two.xhtml",
                       "notes.xhtml", "acknowledgements.xhtml", "about.xhtml"]
    ncx = book.text("toc.ncx")
    for excluded in ("titlepage.xhtml", "copyright.xhtml", "imprint.xhtml", "halftitlepage.xhtml", "nav.xhtml"):
        assert excluded not in entries and excluded not in ncx


def contents_labels(book) -> list[str]:
    toc = re.search(r'<nav epub:type="toc".*?</nav>', book.text("nav.xhtml"), re.S).group(0)
    return re.findall(r'<a href="[^"]+">([^<]*)</a>', toc)


def test_contents_labels_are_plain_titles_by_default(build_book):
    from phoenix_ebook.models import Author
    book = build_book(post(slug="one", title="One", authors=[Author("A")]), placeholders=False)
    assert contents_labels(book) == ["One"]


def test_flaming_hydra_contents_show_authors_but_pages_keep_plain_titles(build_book):
    from phoenix_ebook.models import Author
    posts = [post(slug="one", title="One", authors=[Author("Ann")]),
             post(slug="two", title="Two", authors=[Author("Ann"), Author("Bo"), Author("Cy")]),
             post(slug="three", title="Three")]
    book = build_book(posts, processor="flaminghydra", placeholders=False)
    assert contents_labels(book) == ["One — Ann", "Two — Ann, Bo and Cy", "Three"]
    assert "One — Ann" in book.text("toc.ncx")
    assert "<title>One</title>" in book.chapter("one") and "<h2>One</h2>" in book.chapter("one")


def test_headings(build_book):
    book = build_book(TWO_POSTS)
    pages = [n for n in reading_order(book) if n != "nav.xhtml"]
    h1_pages = [n for n in pages if "<h1>" in book.text(n)]
    assert h1_pages == ["titlepage.xhtml"]
    assert "<h2>One</h2>" in book.chapter("one")
    assert "<h2>Foreword</h2>" in book.text("foreword.xhtml")  # placeholder headings are h2


def test_landmarks_are_hidden_from_the_contents_page(build_book):
    nav = build_book(TWO_POSTS).text("nav.xhtml")
    assert '<nav epub:type="landmarks" hidden="hidden">' in nav
    assert '<nav epub:type="toc"' in nav and 'epub:type="toc" hidden' not in nav


def test_landmarks(build_book):
    assert landmarks(build_book(TWO_POSTS)) == {
        "titlepage": "titlepage.xhtml", "toc": "nav.xhtml", "bodymatter": "one.xhtml",
    }


@pytest.mark.parametrize("field", ["cover", "foreword_file", "intro_file", "about_file"])
def test_missing_content_file_raises_before_building(build_book, tmp_path, field):
    with pytest.raises(MissingContentFile, match="file not found"):
        build_book(TWO_POSTS, **{field: str(tmp_path / "nope.html")})
    assert not list(tmp_path.glob("*.epub"))


@pytest.mark.parametrize("flag", ["--foreword-file", "--cover"])
def test_cli_missing_file_exits_with_message_before_fetching(monkeypatch, tmp_path, capsys, flag):
    fetched = []
    monkeypatch.setattr(build_epub, "get_platform", lambda name: fetched.append(name))
    out = tmp_path / "book.epub"
    with pytest.raises(SystemExit) as exit_info:
        build_epub.main([flag, "does-not-exist.html", "--admin-key", "k:00", "--output", str(out), "slug"])
    assert exit_info.value.code not in (0, None)
    assert flag in str(exit_info.value.code) and "does-not-exist.html" in str(exit_info.value.code)
    assert not fetched and not out.exists()
