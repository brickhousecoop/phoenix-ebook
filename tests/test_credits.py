"""The Phoenix Library Service section that ends "About This Book" in every book (#50)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from conftest import post
from phoenix_ebook.credits import CREDITS_FILE, credits_section


# ---------------------------------------------------------------- the file itself (anyone may edit it)

def _body() -> str:
    return re.sub(r"<!--.*?-->", "", CREDITS_FILE.read_text(encoding="utf-8"), flags=re.S).strip()


def test_credits_file_is_well_formed_xhtml():
    ET.fromstring(f"<div>{_body()}</div>")  # raises on <br>, unquoted attributes, unclosed tags …


def test_credits_file_starts_with_an_h3_and_has_no_bigger_heading():
    body = _body()
    assert body.startswith("<h3>")
    assert not re.search(r"<h[12][\s>]", body), "the page's own heading is the h2; use h3 or smaller"


def test_credits_file_has_no_scripts_or_styles():
    body = _body().lower()
    for banned in ("<script", "<style", "style=", "align=", "font-family"):
        assert banned not in body, f"{banned}: use class=\"centered\" (styled in phoenix.css) instead"


def test_every_credits_image_exists_and_has_alt_text():
    root = ET.fromstring(f"<div>{_body()}</div>")
    images = root.iter("img")
    for img in images:
        assert (CREDITS_FILE.parent / img.get("src")).is_file(), img.get("src")
        assert (img.get("alt") or "").strip(), f"{img.get('src')}: give it alt text"


def test_section_points_images_into_the_book():
    credits = credits_section()
    assert credits.html.startswith('<section class="phoenix-credits">') and "<!--" not in credits.html
    assert 'src="images/credits-phoenix.png"' in credits.html
    assert [p.name for p in credits.images] == ["phoenix.png"] and credits.described == 1


# ---------------------------------------------------------------- in the book

def test_every_book_ends_with_the_section(build_book):
    book = build_book(post())
    about = book.text("about.xhtml")
    assert "<h2>About This Book</h2>" in about and "placeholder" not in about
    assert about.index("<h2>About This Book</h2>") < about.index("The Phoenix Library Service")
    assert "Maria Bustillos" in about and 'src="images/credits-phoenix.png"' in about
    assert "EPUB/images/credits-phoenix.png" in book.zip.namelist()


def test_section_follows_the_editors_about_text(build_book, tmp_path):
    about_file = tmp_path / "about.html"
    about_file.write_text("<h2>About This Book</h2><p>Our own words.</p>")
    about = build_book(post(), about_file=str(about_file)).text("about.xhtml")
    assert about.count("About This Book</h2>") == 1
    assert about.index("Our own words.") < about.index('<section class="phoenix-credits">')


def test_section_is_there_even_without_placeholders(build_book):
    book = build_book(post(), placeholders=False)
    assert "The Phoenix Library Service" in book.text("about.xhtml")


def test_the_image_is_not_reported_as_missing_alt_text(build_book):
    book = build_book(post())
    assert not [p for p in book.result.problems if "credits" in (p.url or "")]
