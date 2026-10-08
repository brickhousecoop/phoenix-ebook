"""Content files as .html, .txt, .md and .docx, for the CLI and the website (#26)."""
from __future__ import annotations

import shutil

import pytest

import build_epub
from conftest import REPO, assert_valid_epub, post
from phoenix_ebook.content import load_content
from phoenix_ebook.errors import InvalidBookSpec

FIXTURES = REPO / "tests" / "fixtures"


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------- each format

def test_txt_is_escaped_paragraphs_under_the_title(tmp_path):
    html = load_content(write(tmp_path, "c.txt", "Arts & Letters <club>\n\n  Second line  \n"), "Copyright").html
    assert html == "<h2>Copyright</h2>\n<p>Arts &amp; Letters &lt;club&gt;</p><p>Second line</p>"


def test_txt_that_starts_with_a_tag_is_still_text(tmp_path):
    assert "&lt;b&gt;bold&lt;/b&gt;" in load_content(write(tmp_path, "c.txt", "<b>bold</b>"), "Notes").html


def test_html_is_inserted_as_written(tmp_path):
    html = "<h3>My own</h3><p style='x'>As is</p>"
    for name in ("c.html", "c.htm", "c.xhtml"):
        assert load_content(write(tmp_path, name, html), "Notes").html == html


def test_markdown(tmp_path):
    html = load_content(str(FIXTURES / "notes.md"), "Notes").html
    assert html.startswith("<h2>Notes</h2>")  # "# Notes" moved down a level
    assert "<h3>Sources</h3>" in html
    assert "<em>Flaming Hydra</em>" in html and "<strong>forty writers</strong>" in html
    assert '<a href="https://flaminghydra.com/">flaminghydra.com</a>' in html
    assert "<ul>" in html and "<ol>" in html
    assert "Arts &amp; Letters &lt;club&gt;" in html  # raw HTML isn't passed through


def test_markdown_without_a_heading_gets_the_title(tmp_path):
    assert load_content(write(tmp_path, "f.md", "Just *text*."), "Foreword").html == \
        "<h2>Foreword</h2><p>Just <em>text</em>.</p>"


def test_word():
    page = load_content(str(FIXTURES / "foreword.docx"), "Foreword")
    assert page.html.startswith("<h2>Foreword</h2>")  # Word's Heading 1
    assert "<h3>What's inside</h3>" in page.html
    assert "<em>Flaming Hydra</em>" in page.html and "<strong>its writers</strong>" in page.html
    assert '<a href="https://flaminghydra.com/">flaminghydra.com</a>' in page.html
    assert "<ul><li>Essays</li>" in page.html
    assert "Arts &amp; Letters &lt;club&gt;" in page.html
    assert "style=" not in page.html and "<img" not in page.html
    assert page.images_left_out == 1


def test_unknown_extensions_behave_as_before(tmp_path):
    plain = write(tmp_path, "intro", "Hello & welcome")
    assert load_content(plain, "Notes").html == "Hello & welcome"  # other sections: inserted as before
    assert load_content(plain, "Introduction", guess_text=True).html == \
        "<h2>Introduction</h2>\n<p>Hello &amp; welcome</p>"  # the introduction guessed plain text (now escaped)
    assert load_content(write(tmp_path, "i.inc", "<p>Hi</p>"), "Introduction", guess_text=True).html == "<p>Hi</p>"


def test_empty_file_is_an_empty_page(tmp_path):
    assert load_content(write(tmp_path, "e.md", "  \n"), "Notes").html == ""


# ---------------------------------------------------------------- in a book

def test_every_section_takes_every_format(build_book, tmp_path):
    files = {
        "copyright_file": write(tmp_path, "copyright.txt", "© 2026 Arts & Letters"),
        "imprint_file": write(tmp_path, "imprint.html", "<h2>Imprint</h2><p>First edition</p>"),
        "foreword_file": shutil.copy(FIXTURES / "foreword.docx", tmp_path / "foreword.docx"),
        "intro_file": write(tmp_path, "intro.md", "Welcome to **the book**."),
        "notes_file": shutil.copy(FIXTURES / "notes.md", tmp_path / "notes.md"),
        "acknowledgements_file": write(tmp_path, "thanks.txt", "Thank you"),
        "about_file": write(tmp_path, "about.md", "# About\n\nMade with phoenix-ebook."),
    }
    built = build_book(post(), **{k: str(v) for k, v in files.items()})
    assert "&lt;club&gt;" in built.text("foreword.xhtml") and "<h3>What's inside</h3>" in built.text("foreword.xhtml")
    assert "<strong>the book</strong>" in built.text("intro.xhtml")
    assert "© 2026 Arts &amp; Letters" in built.text("copyright.xhtml")
    (problem,) = [p for p in built.result.problems if p.kind == "content-image-left-out"]
    assert "1 image in the Foreword file (foreword.docx)" in problem.detail
    assert_valid_epub(built.path)


def test_cli_reads_the_new_formats(tmp_path):
    args = build_epub.build_parser().parse_args(["--foreword-file", str(FIXTURES / "foreword.docx"), "s"])
    assert build_epub._spec_from_args(args).foreword_file.endswith("foreword.docx")


def test_a_renamed_doc_file_is_rejected_before_building(build_book, tmp_path):
    fake = tmp_path / "old.docx"
    fake.write_bytes(b"\xd0\xcf\x11\xe0 an old .doc file")
    with pytest.raises(InvalidBookSpec, match="isn't a Word file") as exc:
        build_book(post(), foreword_file=str(fake))
    assert exc.value.field == "foreword_file"
