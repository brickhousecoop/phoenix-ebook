"""Book metadata: credits, sort names, subtitle, series, rights, identifier (#5)."""
from __future__ import annotations

import re

import pytest

import build_epub
from conftest import assert_valid_epub, post
from phoenix_ebook.epub_builder import InvalidBookSpec, sort_name
from phoenix_ebook.models import Author


def metadata(book) -> str:
    opf = book.text("content.opf")
    return opf[opf.index("<metadata"):opf.index("</metadata>")]


def credits(book, element: str) -> list[tuple[str, str, str, str | None]]:
    """[(id, name, role, file-as)] for dc:creator or dc:contributor, in order."""
    meta = metadata(book)
    out = []
    for item_id, name in re.findall(rf'<dc:{element} id="([^"]+)">([^<]+)</dc:{element}>', meta):
        role = re.search(rf'<meta refines="#{item_id}" property="role" scheme="marc:relators">(\w+)</meta>', meta)
        file_as = re.search(rf'<meta refines="#{item_id}" property="file-as">([^<]+)</meta>', meta)
        out.append((item_id, name, role.group(1) if role else None, file_as.group(1) if file_as else None))
    return out


A, B = Author("Ann Lee"), Author("J.D. Connor", "https://x/jd/")
POSTS = [post(slug="one", authors=[A]), post(slug="two", authors=[B]), post(slug="three", authors=[A])]


# ---------------------------------------------------------------- credits

def test_editor_is_creator_and_post_authors_are_contributors(build_book):
    book = build_book(POSTS, editor="Flaming Hydra Editors")
    assert credits(book, "creator") == [("editor", "Flaming Hydra Editors", "edt", None)]
    assert credits(book, "contributor") == [
        ("contributor-1", "Ann Lee", "aut", "Lee, Ann"),
        ("contributor-2", "J.D. Connor", "aut", "Connor, J.D."),
    ]


def test_without_editor_post_authors_are_creators(build_book):
    book = build_book(POSTS)
    assert credits(book, "creator") == [
        ("author-1", "Ann Lee", "aut", "Lee, Ann"),
        ("author-2", "J.D. Connor", "aut", "Connor, J.D."),
    ]
    assert credits(book, "contributor") == []


def test_no_editor_and_no_post_authors_means_no_credits(build_book):
    book = build_book(post())
    assert credits(book, "creator") == [] and credits(book, "contributor") == []


@pytest.mark.parametrize("name, expected", [
    ("J.D. Connor", "Connor, J.D."),
    ("Felipe De La Hoz", "Hoz, Felipe De La"),  # the automatic rule; overrides fix such names
    ("Madonna", "Madonna"),
    ("Williams, Ian", "Williams, Ian"),
])
def test_sort_name_rule(name, expected):
    assert sort_name(name, {}) == expected


def test_sort_name_overrides_apply_to_contributors_and_editor(build_book):
    posts = [post(slug="one", authors=[Author("Felipe De La Hoz")])]
    overrides = {"Felipe De La Hoz": "De La Hoz, Felipe", "The Editors": "Editors, The"}
    book = build_book(posts, editor="The Editors", sort_names=overrides)
    assert credits(book, "creator") == [("editor", "The Editors", "edt", "Editors, The")]
    assert credits(book, "contributor")[0][3] == "De La Hoz, Felipe"


# ---------------------------------------------------------------- titles

def test_subtitle_is_main_subtitle_and_expanded_titles(build_book):
    meta = metadata(build_book(POSTS, title="Flaming Hydra", subtitle="September 2025"))
    assert '<dc:title id="title">Flaming Hydra</dc:title>' in meta
    assert '<dc:title id="subtitle">September 2025</dc:title>' in meta
    assert '<dc:title id="fulltitle">Flaming Hydra: September 2025</dc:title>' in meta
    for item_id, kind in [("title", "main"), ("subtitle", "subtitle"), ("fulltitle", "expanded")]:
        assert f'<meta refines="#{item_id}" property="title-type">{kind}</meta>' in meta


def test_without_subtitle_a_single_title(build_book):
    meta = metadata(build_book(POSTS, title="Plain"))
    assert re.findall(r"<dc:title[^>]*>([^<]+)</dc:title>", meta) == ["Plain"]
    assert "title-type" not in meta


def test_subtitle_and_series_on_title_and_half_title_pages(build_book):
    book = build_book(POSTS, title="Flaming Hydra", subtitle="September 2025",
                      series="Flaming Hydra Digest", series_number="367", editor="Eds")
    title_page = book.text("titlepage.xhtml")
    order = re.findall(r'<h1>|class="(subtitle|series|editor)"', title_page)
    assert [o or "h1" for o in order] == ["h1", "subtitle", "series", "editor"]
    assert '<p class="series">Flaming Hydra Digest · No. 367</p>' in title_page
    assert '<p class="subtitle">September 2025</p>' in book.text("halftitlepage.xhtml")


# ---------------------------------------------------------------- series, rights, identifier

def test_series_with_number(build_book):
    meta = metadata(build_book(POSTS, series="Flaming Hydra Digest", series_number="367"))
    assert '<meta property="belongs-to-collection" id="series">Flaming Hydra Digest</meta>' in meta
    assert '<meta refines="#series" property="collection-type">series</meta>' in meta
    assert '<meta refines="#series" property="group-position">367</meta>' in meta


def test_series_without_number(build_book):
    book = build_book(POSTS, series="Digest")
    assert "group-position" not in metadata(book)
    assert '<p class="series">Digest</p>' in book.text("titlepage.xhtml")


def test_series_number_without_series_is_an_error(build_book):
    with pytest.raises(InvalidBookSpec, match="series"):
        build_book(POSTS, series_number="3")


def test_rights_only_when_given(build_book):
    assert "<dc:rights>© 2026 Flaming Hydra</dc:rights>" in metadata(build_book(POSTS, rights="© 2026 Flaming Hydra"))
    assert "dc:rights" not in metadata(build_book(POSTS))


@pytest.mark.parametrize("isbn, urn, isbn_type", [
    ("979-8-99-202554-5", "urn:isbn:9798992025545", "15"),
    ("0 306 40615 2", "urn:isbn:0306406152", "02"),
])
def test_isbn_identifier(build_book, isbn, urn, isbn_type):
    meta = metadata(build_book(POSTS, isbn=isbn))
    assert f'<dc:identifier id="id">{urn}</dc:identifier>' in meta
    assert f'<meta refines="#id" property="identifier-type" scheme="onix:codelist5">{isbn_type}</meta>' in meta


def test_isbn10_with_x_check_digit(build_book):
    assert '<dc:identifier id="id">urn:isbn:080442957X</dc:identifier>' in metadata(build_book(POSTS, isbn="0-8044-2957-x"))


@pytest.mark.parametrize("isbn", ["979-8-99-202554-6", "0306406151", "12345", "978-0-306-40615-X"])
def test_invalid_isbn_is_an_error(build_book, isbn):
    with pytest.raises(InvalidBookSpec, match="not a valid ISBN"):
        build_book(POSTS, isbn=isbn)


def test_issn_identifies_the_series(build_book):
    book = build_book(POSTS, series="Flaming Hydra Digest", series_number="368", issn="03178471")
    assert '<meta refines="#series" property="dcterms:identifier">urn:issn:0317-8471</meta>' in metadata(book)
    assert '<p class="series">Flaming Hydra Digest · No. 368 · ISSN 0317-8471</p>' in book.text("titlepage.xhtml")
    assert "urn:issn" not in re.search(r"<dc:identifier[^>]*>[^<]*</dc:identifier>", metadata(book)).group(0)


def test_issn_with_x_check_digit_is_normalized():
    from phoenix_ebook.epub_builder import normalize_issn
    assert normalize_issn("2434-561x") == "2434-561X"
    assert normalize_issn("0317-8472") is None and normalize_issn("1234") is None


def test_issn_without_series_is_an_error(build_book):
    with pytest.raises(InvalidBookSpec, match="needs --series"):
        build_book(POSTS, issn="0317-8471")


def test_invalid_issn_is_an_error(build_book):
    with pytest.raises(InvalidBookSpec, match="not a valid ISSN"):
        build_book(POSTS, series="S", issn="0317-8472")


def test_uuid_identifier_without_isbn(build_book):
    assert re.search(r'<dc:identifier id="id">urn:uuid:[0-9a-f-]{36}</dc:identifier>', metadata(build_book(POSTS)))


# ---------------------------------------------------------------- CLI

def test_editor_and_author_flags_agree(captured_spec):
    assert captured_spec(["--editor", "E", "s"]).editor == captured_spec(["--author", "E", "s"]).editor == "E"
    assert captured_spec(["--editor", "E", "--author", "E", "s"]).editor == "E"


def test_conflicting_editor_and_author_is_an_error(captured_spec):
    with pytest.raises(SystemExit) as exit_info:
        captured_spec(["--editor", "E", "--author", "F", "s"])
    assert "--editor and --author" in str(exit_info.value.code)


def test_manifest_author_and_editor_conflict(tmp_path, captured_spec):
    path = tmp_path / "b.toml"
    path.write_text('posts=["s"]\n[book]\ntitle="t"\neditor="E"\nauthor="F"\n'
                    '[source]\nplatform="ghost"\nurl="https://flaminghydra.ghost.io"\n')
    with pytest.raises(SystemExit) as exit_info:
        captured_spec(["--manifest", str(path)])
    assert "[book] editor and author" in str(exit_info.value.code)


def test_cli_series_number_without_series_exits_before_fetching(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(build_epub, "get_platform", lambda name: fetched.append(name))
    with pytest.raises(SystemExit) as exit_info:
        build_epub.main(["--series-number", "3", "--admin-key", "k:00", "--output", str(tmp_path / "b.epub"), "s"])
    assert "series" in str(exit_info.value.code) and not fetched


@pytest.fixture
def captured_spec(monkeypatch):
    """Run main() with argv and return the BookSpec it would build."""
    specs = []
    monkeypatch.setattr(build_epub, "_run_build", lambda spec, override_secret: specs.append(spec))

    def run(argv):
        build_epub.main(argv)
        return specs[-1]
    return run


# ---------------------------------------------------------------- validity

def test_book_without_editor_is_valid(build_book):
    assert_valid_epub(build_book(POSTS, subtitle="S").path)
