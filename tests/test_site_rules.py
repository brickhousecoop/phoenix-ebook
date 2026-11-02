"""Shared cleanup rules, and the plain files that add to them (#43)."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import phoenix_ebook.processors  # noqa: F401 — registers the built-ins
from conftest import REPO, assert_valid_epub, post
from phoenix_ebook.errors import SiteFileError
from phoenix_ebook.models import Post
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.processors import base, rules
from phoenix_ebook.processors.base import get_processor, register_site_files, select_processor
from phoenix_ebook.processors.rules import load_site_rules

SHIPPED = sorted((REPO / "phoenix_ebook" / "sites").glob("*.toml"))


def write(folder, name, text):
    (folder / f"{name}.toml").write_text(text, encoding="utf-8")


def rules_from(folder, text):
    write(folder, "blog", text)
    return load_site_rules("blog", folder)


def visible(html: str, processor: str = "generic") -> str:
    html = GhostPlatform().normalize_html(html)
    soup = BeautifulSoup(html, "html.parser")
    get_processor(processor).clean(soup, Post(slug="p", title="t", html=html))
    for marker in soup.find_all("span", attrs={rules.CALL_TO_ACTION_REMOVED: True}):
        marker.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


@pytest.fixture
def registry(monkeypatch):
    """Registrations made by a test are forgotten afterwards."""
    monkeypatch.setattr(base, "_REGISTRY", dict(base._REGISTRY))


# ---------------------------------------------------------------- phrases

@pytest.mark.parametrize("phrase, line", [
    ("give a gift subscription", "Give a gift subscription"),
    ("give a gift subscription", "GIVE A GIFT SUBSCRIPTION!"),
    ("give a gift subscription", "give  a gift\nsubscription."),
    ("get #% off *", "Get 20% off for 1 year"),
    ("get #% off *", "Get 20% off"),
    ("# day free trial", "7 day free trial"),
    ("subscribe - *", "Subscribe - first month FREE"),
    ("our blog is reader-supported *", "Our blog is reader-supported. To receive new posts, subscribe."),
    ("* is a newsletter", "Hell World is a newsletter."),
])
def test_phrase_matches_the_whole_line(tmp_path, phrase, line):
    assert rules_from(tmp_path, f'remove_lines = ["{phrase}"]').sells(line)


@pytest.mark.parametrize("phrase, line", [
    ("give a gift subscription", "Why not give a gift subscription to a friend?"),
    ("get #% off *", "Get some off for 1 year"),
    ("get #% off *", "Get 20% offal"),
    ("# day free trial", "A day free trial"),
    ("subscribe", "Subscriber"),
])
def test_phrase_does_not_match_other_lines(tmp_path, phrase, line):
    assert not rules_from(tmp_path, f'remove_lines = ["{phrase}"]').sells(line)


def test_regular_expression_characters_are_plain_text(tmp_path):
    loaded = rules_from(tmp_path, 'remove_lines = ["only (today) [ok]"]')
    assert loaded.sells("Only (today) [ok]") and not loaded.sells("Only today k")


def test_a_phrase_never_matches_a_long_line():
    long_line = "Subscribe - " + "word " * rules.LINE_MAX_WORDS
    assert long_line.strip() in visible(f"<p>{long_line}</p>")


# ---------------------------------------------------------------- defaults, for a blog with no file

@pytest.mark.parametrize("line", ["Subscribe now", "Subscribe - first month FREE", "Get 20% off for 1 year",
                                  "33% off one year", "7 day free trial", "Give a gift subscription", "Share",
                                  "Subscribe to read the rest."])
def test_defaults_remove_lines_for_a_blog_with_no_file(line):
    assert visible(f"<p>Before.</p><p>{line}</p><p>After.</p>") == "Before. After."


def test_defaults_remove_a_paragraph_of_only_a_sign_up_link():
    html = '<p>Before.</p><p><a href="https://example.org/#/portal/signup">Anything at all</a></p>'
    assert visible(html) == "Before."


def test_defaults_remove_share_buttons_and_keep_others():
    button = '<div class="kg-card kg-button-card"><a href="{}" class="kg-btn">{}</a></div>'
    html = (button.format("https://bsky.app/intent/compose?text=x", "Share on Bluesky")
            + button.format("https://example.org/book", "Read the book"))
    assert visible(html) == "Read the book"


def test_defaults_leave_quotes_and_longer_sentences():
    assert visible("<blockquote><p>Subscribe now</p></blockquote>") == "Subscribe now"
    assert visible("<p>Should you subscribe now?</p><p>Reader, share.</p>") == "Should you subscribe now? Reader, share."


def test_a_new_ghost_site_builds_with_the_defaults(build_book):
    html = GhostPlatform().normalize_html("<p>Start.</p><p>Subscribe now</p><p>End.</p>")
    book = build_book(post(html), processor="generic")
    assert "Subscribe now" not in book.chapter()
    assert [p.detail for p in book.result.problems if p.kind == "call-to-action"] == ['sales line removed: "Subscribe now"']
    assert_valid_epub(book.path)


# ---------------------------------------------------------------- a blog's file

@pytest.fixture
def sites(tmp_path, monkeypatch):
    write(tmp_path, "_defaults", 'remove_lines = ["subscribe now", "share"]')
    monkeypatch.setattr(rules, "SITES_DIR", tmp_path)
    monkeypatch.setattr(base, "SITES_DIR", tmp_path)
    return tmp_path


def test_a_file_adds_to_the_defaults_and_keep_lines_turns_one_off(sites):
    loaded = rules_from(sites, 'remove_lines = ["buy my book"]\nkeep_lines = ["share"]')
    assert loaded.sells("Buy my book") and loaded.sells("Subscribe now")
    assert not loaded.sells("Share")


def test_keep_lines_also_keeps_a_line_of_only_a_sign_up_link(sites, registry):
    write(sites, "blog", 'platform = "ghost"\nsites = ["blog.example"]\nkeep_lines = ["join us"]')
    register_site_files()
    html = '<p><a href="https://blog.example/#/portal/signup">Join us</a></p>'
    assert visible(html, "blog") == "Join us"


def test_a_blog_can_be_only_a_file(sites, registry):
    write(sites, "blog", 'platform = "ghost"\nsites = ["blog.example"]\nremove_lines = ["buy my book"]\n'
                         'remove_all_buttons = true')
    register_site_files()
    assert select_processor("ghost", "www.blog.example", "generic") == "blog"
    assert select_processor("other", "www.blog.example", "generic") == "generic"
    button = '<div class="kg-card kg-button-card"><a href="https://x.example/" class="kg-btn">OK FINE</a></div>'
    assert visible(f"<p>Keep.</p><p>Buy my book.</p>{button}<p>Subscribe now</p>", "blog") == "Keep."


def test_a_file_does_not_replace_a_python_processor(sites, registry):
    write(sites, "flaminghydra", 'remove_lines = ["buy my book"]')
    register_site_files()
    assert isinstance(get_processor("flaminghydra"), phoenix_ebook.processors.flaminghydra.FlamingHydraProcessor)
    assert get_processor("flaminghydra").rules.sells("Buy my book")


@pytest.mark.parametrize("setting, entry, href, matches", [
    ("sales_links", "blog.example/subscribe", "https://www.blog.example/subscribe/", True),
    ("sales_links", "blog.example/subscribe", "http://blog.example/subscribers", False),
    ("sales_links", "/subscribe", "/subscribe", True),
    ("promo_links", "kickstarter.com/projects/me/", "https://www.kickstarter.com/projects/me/thing", True),
    ("promo_links", "kickstarter.com/projects/me/", "https://www.kickstarter.com/projects/you/thing", False),
])
def test_addresses_match_from_the_start(tmp_path, setting, entry, href, matches):
    loaded = rules_from(tmp_path, f'{setting} = ["{entry}"]')
    assert (loaded.is_sales_link if setting == "sales_links" else loaded.is_promo_link)(href) is matches


def test_sales_links_lose_the_link_in_sentences_and_take_final_sections(sites, registry):
    write(sites, "blog", 'platform = "ghost"\nsites = ["blog.example"]\nsales_links = ["blog.example/x"]')
    register_site_files()
    processor = get_processor("blog")
    soup = BeautifulSoup('<p>Do <a href="https://blog.example/x">join</a> us.</p><hr><p>End.</p>', "html.parser")
    processor.clean(soup, Post(slug="p", title="t", html=""))
    assert soup.find("a") is None and "Do join us." in " ".join(soup.get_text(" ").split())
    assert visible('<p>Start.</p><hr><p>Writing, and <a href="https://blog.example/x">join</a>.</p>', "blog") == "Start."


def test_promo_links_stay_in_sentences_and_take_only_sections_made_up_of_them(sites, registry):
    write(sites, "blog", 'platform = "ghost"\nsites = ["blog.example"]\npromo_links = ["blog.example/x"]')
    register_site_files()
    link = '<a href="https://blog.example/x">the campaign</a>'
    assert visible(f"<p>See {link} today.</p><p>More.</p>", "blog") == "See the campaign today. More."
    assert visible(f"<p>Start.</p><hr><p>Back {link}!</p>", "blog") == "Start."
    mixed = f"<p>Start.</p><hr><p>Real writing here.</p><p>Back {link}!</p>"
    assert "Real writing here." in visible(mixed, "blog")


def test_button_links_take_only_buttons(sites, registry):
    write(sites, "blog", 'platform = "ghost"\nsites = ["blog.example"]\nbutton_links = ["shop.example"]')
    register_site_files()
    button = '<div class="kg-card kg-button-card"><a href="https://shop.example/a" class="kg-btn">Shop</a></div>'
    link = '<a href="https://shop.example/a">the shop</a>'
    assert visible(f"<p>Start.</p>{button}<p>Visit {link}.</p>", "blog") == "Start. Visit the shop ."
    assert "the shop" in visible(f"<p>Start.</p><hr><p>Visit {link}.</p>", "blog")


# ---------------------------------------------------------------- mistakes

@pytest.mark.parametrize("text, words", [
    ('remove_line = ["x"]', "unknown setting remove_line"),
    ('remove_lines = "buy my book"', "remove_lines must be a list"),
    ("remove_lines = [1]", "remove_lines must be a list"),
    ('remove_lines = [""]', "remove_lines must be a list"),
    ('remove_lines = ["*"]', "has no words"),
    ('remove_lines = ["#"]', "has no words"),
    ('keep_lines = ["* *"]', "has no words"),
    ('sales_links = [" "]', "sales_links must be a list"),
    ('remove_all_buttons = "yes"', "remove_all_buttons must be true or false"),
    ("remove_lines = [", "can't be read"),
])
def test_a_mistake_stops_with_a_clear_error_naming_the_file(tmp_path, text, words):
    with pytest.raises(SiteFileError) as caught:
        rules_from(tmp_path, text)
    assert words in str(caught.value) and "blog.toml" in str(caught.value)


@pytest.mark.parametrize("text", ['platform = "ghost"\nsites = ["blog.example"]\nremove_line = ["x"]',
                                  "not toml [", 'remove_lines = ["x"]'])
def test_a_broken_file_stops_only_that_blog(sites, registry, text):
    write(sites, "blog", text)
    write(sites, "fine", 'platform = "ghost"\nsites = ["fine.example"]')
    register_site_files()
    assert select_processor("ghost", "blog.ghost.io", "generic") == "blog"
    with pytest.raises(SiteFileError):
        get_processor("blog")
    assert get_processor("fine").name == "fine"
    assert get_processor("generic").name == "generic"
    assert select_processor("ghost", "other.ghost.io", "generic") == "generic"


def test_a_broken_file_for_a_python_blog_stops_only_that_blog(sites, registry):
    write(sites, "flaminghydra", "not toml [")
    with pytest.raises(SiteFileError):
        get_processor("flaminghydra")
    assert get_processor("generic").name == "generic"


# ---------------------------------------------------------------- the files in the repo

def test_the_repo_has_the_expected_files():
    assert {p.name for p in SHIPPED} >= {"_defaults.toml", "flaminghydra.toml", "hellworld.toml"}


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_every_file_in_the_repo_is_usable(path):
    load_site_rules(path.stem)
    assert path.stem.startswith("_") or get_processor(path.stem).name == path.stem
