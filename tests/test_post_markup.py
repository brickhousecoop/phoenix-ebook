"""Post markup that made books invalid: empty headings/ids/links, broken links, byline headings (#22)."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import build_epub
import phoenix_ebook.processors  # noqa: F401 — registers flaminghydra
from conftest import assert_valid_epub, post
from phoenix_ebook.models import Author, BuildProblem, Post
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.processors.base import get_processor

CARRIE = Author("Carrie Frye", "https://flaminghydra.com/contributor/carrie-frye/")


def cleaned(html: str, processor: str = "generic", authors=()) -> BeautifulSoup:
    html = GhostPlatform().normalize_html(html)
    soup = BeautifulSoup(html, "html.parser")
    get_processor(processor).clean(soup, Post(slug="p", title="t", html=html, authors=list(authors)))
    return soup


def link_change(soup, href_or_text: str):
    """(kind, original, new, reason) of the changed link whose new href or text is given."""
    for a in soup.find_all("a", attrs={"data-link-repaired": True}):
        if href_or_text in (a["href"], a.get_text()):
            return "repaired", a["data-link-repaired"], a["href"], a["data-link-reason"]
    for span in soup.find_all("span", attrs={"data-link-unlinked": True}):
        if span.get_text() == href_or_text:
            return "unlinked", span["data-link-unlinked"], None, span["data-link-reason"]
    return None


# ---------------------------------------------------------------- empty headings, ids, links

def test_empty_headings_and_ids_removed():
    # Real markup: Flaming Hydra's section title, byline and an empty spacer heading.
    soup = cleaned('<h2 id="the-stigmata">The Stigmata</h2><h4 id=""></h4><h4 id="-1"></h4>'
                   '<h3 id=""><img src="https://img.test/a.png" alt="A"></h3><p id="">Text</p>')
    assert [h.name for h in soup.find_all(["h2", "h3", "h4"])] == ["h2", "h3"]  # the heading with an image stays
    assert soup.find(id="") is None and soup.p.get_text() == "Text"


def test_empty_links_removed_and_marked():
    soup = cleaned('<p>See <a href="https://example.org/x" rel="noreferrer"></a>this and '
                   '<a href="https://example.org/y"><img src="https://img.test/a.png" alt="A"></a>.</p>')
    assert soup.find("a", href="https://example.org/x") is None
    assert soup.find("span", attrs={"data-link-removed": "https://example.org/x"}) is not None
    assert soup.find("a", href="https://example.org/y") is not None  # image links are not empty


def test_ordinary_links_and_targets_untouched():
    html = ('<h2 id="alien-art">Alien Art</h2><p><a href="#alien-art">Alien Art</a> and '
            '<a href="https://example.org/a?b=1&amp;c=d#e">this</a> and <a href="mailto:hello@example.org">mail</a> '
            'and <a href="/about">about</a> and <a href="https://de.wikipedia.org/wiki/Müller">Müller</a>.</p>')
    soup = cleaned(html)
    assert soup.find(attrs={"data-link-repaired": True}) is None and soup.find("span") is None
    assert soup.find("a", href="https://de.wikipedia.org/wiki/Müller")  # IRIs may be non-ASCII


# ---------------------------------------------------------------- in-post section links

DIGEST = ('<p><a href="#{href}"><strong>{text}</strong></a><br><strong>Someone</strong></p><hr>'
          '<h2 id="{target}">{heading}</h2>')


def test_percent_encoded_ids_are_simplified_so_plain_links_work():
    # Ghost percent-encodes curly quotes in ids ("issue-662"); the contents link doesn't.
    soup = cleaned(DIGEST.format(href="an-infinite-plane", text="An Infinite Plane",
                                 target="%E2%80%9Can-infinite-plane%E2%80%9D", heading="“An Infinite Plane”"))
    assert soup.h2["id"] == "an-infinite-plane" and soup.a["href"] == "#an-infinite-plane"
    assert "data-link-repaired" not in soup.a.attrs  # the link itself didn't change


@pytest.mark.parametrize("href, text, target, heading, reason", [
    ("the-d-c-accent", "The D.C. Accent", "the-dc-accent", "The D.C. Accent", "matched a section id"),
    ("polar-impressions", "Polar Impressions", "polar-impressionss", "Polar Impressions", "matched the heading"),
    ("halftiime-in-america", "Halftime in America", "halftime-in-america", "Halftime in America",
     "matched the heading"),
    ("the-long-memory-of-alan-moore-part-ii", "The Long Memory of Alan Moore, Part II",
     "long-memory-of-alan-moore-part-ii", "Long Memory of Alan Moore — Part II", "matched the closest section id"),
])
def test_broken_section_links_repaired(href, text, target, heading, reason):
    soup = cleaned(DIGEST.format(href=href, text=text, target=target, heading=heading))
    link = soup.find("a")
    target_id = soup.find("h2")["id"]
    assert link["href"] == f"#{target_id}"
    assert link["data-link-repaired"] == f"#{href}" and link["data-link-reason"].startswith(reason)


def test_heading_without_id_gets_one():
    soup = cleaned('<p><a href="#transit-2">Transit</a></p><h3>Transit</h3>')
    assert soup.h3["id"] == "transit" and soup.a["href"] == "#transit"


def test_non_ascii_ids_simplified_and_links_follow():
    soup = cleaned('<p><a href="#día-de-los-muertos">Día de los Muertos</a></p>'
                   '<h2 id="día-de-los-muertos">Día de los Muertos</h2>')
    assert soup.h2["id"] == "dia-de-los-muertos"
    assert link_change(soup, "#dia-de-los-muertos") == ("repaired", "#día-de-los-muertos", "#dia-de-los-muertos",
                                                       "section id simplified")


def test_ligatures_spelled_out_in_ids():
    soup = cleaned('<p><a href="#moebius-and-the-art">Mœbius and the Art</a></p>'
                   '<h2 id="m%C5%93bius-and-the-art">Mœbius and the Art</h2>')
    assert soup.h2["id"] == "moebius-and-the-art" and soup.a["href"] == "#moebius-and-the-art"


def test_old_style_name_targets_work():
    # Hand-made footnotes in an HTML card ("Toward a Quantitative Assessment…")
    soup = cleaned('<p>Fun.<a href="#real-sociologists"><sup>14</sup></a></p>'
                   '<p><a name="real-sociologists"><sup>14</sup></a> Real sociologists disagree.</p>')
    assert soup.find(id="real-sociologists") is not None and not soup.find(attrs={"name": True})
    assert soup.find("a", href="#real-sociologists") is not None and soup.find("span") is None


def test_section_link_with_nothing_to_match_is_unlinked():
    soup = cleaned('<p><a href="#the-final-hydranym-vote"><strong>The Final HYDRANYM: Vote!</strong></a></p>'
                   '<h2 id="other">Something Else</h2>')
    assert soup.find("a") is None and soup.strong.get_text() == "The Final HYDRANYM: Vote!"
    assert link_change(soup, "The Final HYDRANYM: Vote!") == (
        "unlinked", "#the-final-hydranym-vote", None, "points to a section that isn't in this post")


# ---------------------------------------------------------------- malformed addresses

@pytest.mark.parametrize("href, expected, reason", [
    (" https://theracket.news/p/interview-president", "https://theracket.news/p/interview-president",
     "spaces or quotes trimmed"),
    ("https://example.org/witches ", "https://example.org/witches", "spaces or quotes trimmed"),
    ("https://blog.pinboard.in/2017/06/pinboard_acquires_delicious/”",
     "https://blog.pinboard.in/2017/06/pinboard_acquires_delicious/", "spaces or quotes trimmed"),
    ("mailto: hello@flaminghydra.com", "mailto:hello@flaminghydra.com", "space after mailto: removed"),
    ("https//flaminghydra.com/about", "https://flaminghydra.com/about", "missing ':' added"),
    ("#flaminghydra.com/poetic-license/", "https://flaminghydra.com/poetic-license/",
     "a web address written as a section link"),
    ("bsky.app/profile/davidjroth.bsky.social/post/3lx732hl2kc2h",
     "https://bsky.app/profile/davidjroth.bsky.social/post/3lx732hl2kc2h", "https:// added"),
    ("https://commons.wikimedia.org/wiki/File:x.jpg#/media/F[…]tors.jpg",
     "https://commons.wikimedia.org/wiki/File:x.jpg#/media/F%5B…%5Dtors.jpg", "characters encoded"),
])
def test_malformed_addresses_repaired(href, expected, reason):
    soup = cleaned(f'<p>See <a href="{href}">the link</a>.</p>')
    assert link_change(soup, "the link") == ("repaired", href, expected, reason)


@pytest.mark.parametrize("href", ["Washington Post, January 29th, 2018",
                                  "[11:22 PM]https://en.wikipedia.org/wiki/Human_capital_flight_from_Nigeria",
                                  "http://bachmann.house.gov/Biography/OfficialPhoto.htm, Public Domain, "
                                  "https://commons.wikimedia.org/"])
def test_non_addresses_unlinked(href):
    soup = cleaned(f'<p>Photo: <a href="{href}">Wikipedia</a></p>')
    assert soup.find("a") is None and soup.p.get_text() == "Photo: Wikipedia"
    assert link_change(soup, "Wikipedia") == ("unlinked", href.strip(), None, "not a web address")


# ---------------------------------------------------------------- Flaming Hydra bylines

def test_digest_byline_heading_becomes_paragraph():
    soup = cleaned('<h2 id="the-stigmata">The Stigmata</h2><h4 id="by-julianne"><em>by</em> <a href="https://'
                   'flaminghydra.com/contributor/julianne-escobedo-shepherd/">Julianne Escobedo Shepherd</a></h4>'
                   '<h4 id=""></h4><p>That I was ever allowed…</p>', processor="flaminghydra")
    assert soup.find("h4") is None
    byline = soup.find("p", class_="byline")
    assert byline.get_text(" ", strip=True) == "by Julianne Escobedo Shepherd" and byline.a is not None
    assert byline["id"] == "by-julianne"


@pytest.mark.parametrize("between", ["<p></p>", '<figure><img src="https://img.test/a.png" alt="A"></figure>'])
def test_byline_after_spacer_or_figure(between):
    soup = cleaned(f'<h2 id="s">Sketch Book</h2>{between}<h4 id="by-jim"><em>by</em> Jim Cooke</h4><p>x</p>',
                   processor="flaminghydra")
    assert soup.find("p", class_="byline") is not None and soup.find("h4") is None


def test_byline_repeating_the_chapter_header_is_removed():
    soup = cleaned('<h4 id="by-carrie-frye"><em>by</em> Carrie Frye</h4><p>Text.</p>', processor="flaminghydra",
                   authors=[CARRIE])
    assert soup.find("h4") is None and soup.find(class_="byline") is None and soup.p.get_text() == "Text."


def test_top_byline_naming_someone_else_is_kept_as_paragraph():
    soup = cleaned('<h4><em>by</em> The Editors</h4><p>Text.</p>', processor="flaminghydra", authors=[CARRIE])
    assert soup.find("p", class_="byline").get_text(" ", strip=True) == "by The Editors"


@pytest.mark.parametrize("html", ['<hr><h2 id="x">By the intensity with which something is being said</h2>',
                                  '<p>Text.</p><h4 id="by-design">By Design</h4><p>More.</p>'])
def test_headings_that_just_start_with_by_stay(html):
    soup = cleaned(html, processor="flaminghydra")
    assert soup.find(class_="byline") is None and soup.find(["h2", "h4"]) is not None


def test_generic_processor_leaves_bylines_but_cleans_links():
    soup = cleaned('<h2 id="s">S</h2><h4 id=""><em>by</em> Jim</h4><h4 id=""></h4>'
                   '<p><a href=" https://example.org">x</a></p>')
    assert soup.find("h4").get_text(" ", strip=True) == "by Jim"  # not a byline paragraph
    assert len(soup.find_all("h4")) == 1 and soup.find("h4").get("id") is None
    assert soup.a["href"] == "https://example.org"


# ---------------------------------------------------------------- report and the built book

def test_every_changed_link_is_reported(build_book):
    html = ('<p><a href="#polar-impressions">Polar Impressions</a> · <a href="#gone">Gone</a> · '
            '<a href=" https://example.org/a">spaced</a> · <a href="https://example.org/b"></a></p>'
            '<h2 id="polar-impressionss">Polar Impressions</h2><p>Text</p>')
    book = build_book(post(GhostPlatform().normalize_html(html)))
    by_kind = {p.kind: p for p in book.result.problems}
    assert set(by_kind) == {"link-repaired", "link-unlinked", "link-empty-removed"}
    repaired = [p for p in book.result.problems if p.kind == "link-repaired"]
    assert len(repaired) == 2
    assert any(p.url == "#polar-impressions" and "now #polar-impressionss" in p.detail
               and "Polar Impressions" in p.detail for p in repaired)
    assert any(p.url == " https://example.org/a" and "spaces or quotes trimmed" in p.detail for p in repaired)
    assert by_kind["link-unlinked"].url == "#gone" and '"Gone"' in by_kind["link-unlinked"].detail
    assert by_kind["link-empty-removed"].url == "https://example.org/b"
    chapter = book.chapter()
    assert "data-link-" not in chapter and "· Gone ·" in chapter and 'href="#gone"' not in chapter


def test_book_with_all_of_it_is_valid(build_book):
    html = ('<p><a href="#an-infinite-plane">An Infinite Plane</a> · <a href="#gone">Gone</a> · '
            '<a href="https://commons.wikimedia.org/wiki/File:x.jpg#/media/F[…]tors.jpg">photo</a> · '
            '<a href="Washington Post, January 29th, 2018">WaPo</a> · <a href="https://example.org/b"></a> · '
            '<a href="#día">Día</a></p><h2 id="%E2%80%9Can-infinite-plane%E2%80%9D">“An Infinite Plane”</h2>'
            '<h4 id="by-x"><em>by</em> Someone</h4><h4 id=""></h4><p>Text.</p><h2 id="día">Día</h2>')
    book = build_book(post(GhostPlatform().normalize_html(html)), processor="flaminghydra")
    assert_valid_epub(book.path)


def test_cli_summary_lines(capsys):
    build_epub._report_problems([
        BuildProblem("link-repaired", "p", "#a", "x"), BuildProblem("link-repaired", "p", "#b", "x"),
        BuildProblem("link-unlinked", "p", "#c", "x"), BuildProblem("link-empty-removed", "p", "https://x", "x"),
    ])
    err = capsys.readouterr().err
    assert "2 links in posts were repaired; check the warnings above" in err
    assert "1 link in posts pointed nowhere and was unlinked (words kept)" in err
    assert "1 empty link was removed" in err


def test_byline_styles_exist():
    from phoenix_ebook.epub_builder import STYLES_DIR
    css = (STYLES_DIR / "phoenix.css").read_text()
    assert 'section[epub|type~="chapter"] p.byline{' in css and "p.byline + p{" in css
