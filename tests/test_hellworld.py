"""Welcome to Hell World: sales lines and buttons come out of the book; writing stays (#38)."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import phoenix_ebook.processors  # noqa: F401 — registers hellworld
from conftest import assert_valid_epub, post
from phoenix_ebook.models import Post
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.processors.base import get_processor

SITE = "https://www.welcometohellworld.com"
BUTTON = '<div class="kg-card kg-button-card"><a href="{href}" class="kg-btn">{label}</a></div>'


def processed(html: str) -> BeautifulSoup:
    html = GhostPlatform().normalize_html(html)
    soup = BeautifulSoup(html, "html.parser")
    get_processor("hellworld").clean(soup, Post(slug="p", title="t", html=html))
    return soup


def words(html: str) -> str:
    return " ".join(BeautifulSoup(str(processed(html)), "html.parser").get_text(" ", strip=True).split())


@pytest.mark.parametrize("line", [
    "Subscribe now", "SUBSCRIBE NOW", "subscribe now", "SUBSCRIBE NOW PLEASE AND THANK YOU",
    "Subscribe - first month FREE", "Subscribe - 50% off first month",
    "Get 20% off for 1 year", "Get 25% off forever", "33% off one year of Hell World", "28% off one year",
    "7 day free trial", "Give a gift subscription", "Support independent media", "Share",
    "Share Welcome to Hell World",
    "Subscribe to read the rest", "Subscribe to read the rest here.", "SUBSCRIBE TO READ THE REST",
    "Subscribe and read the rest here.", "To read the rest of this edition of Hell World please subscribe.",
    "To unlock the rest of this post purchase a subscription thank you.",
    "Welcome to Hell World is a reader-supported publication. To receive new posts and support my work, subscribe.",
])
def test_lines_that_only_sell_are_removed(line):
    assert words(f"<p>Before.</p><p>{line}</p><p>After.</p>") == "Before. After."


REST = "<p>" + "word " * 100 + "</p>"


def test_a_paywall_line_linked_elsewhere_is_removed_when_the_rest_follows():
    html = f'<p>Before.</p><p><a href="{SITE}/some-post/">Subscribe to read the rest here</a>.</p>{REST}'
    assert "Subscribe" not in words(html) and words(html).endswith("word")


@pytest.mark.parametrize("line", [
    'Subscribe to read the rest <a href="{SITE}/full-version/">here</a>.',
    'This is an abridged version. To read the rest please subscribe and go <a href="{SITE}/full-version/">here</a>.',
])
def test_a_paywall_line_stays_when_the_post_really_is_shortened(line):
    soup = processed("<p>Before.</p><p>" + line.replace("{SITE}", SITE) + "</p>")
    assert soup.a["href"] == f"{SITE}/full-version/"
    assert "read the rest" in soup.get_text()


def test_a_paragraph_that_is_only_a_portal_link_is_removed():
    html = f'<p>Before.</p><p><a href="{SITE}/#/portal/signup?">Whatever it says</a></p><p>After.</p>'
    assert words(html) == "Before. After."


@pytest.mark.parametrize("href, label", [
    (f"{SITE}/come-on-man/", "OK FINE"),
    (f"{SITE}/come-on-man/", "$41.40 for one year!!!"),
    ("https://orbooks.com/catalog/we-had-it-coming/", "ORDER WE HAD IT COMING NOW"),
])
def test_every_button_is_removed(href, label):
    html = "<p>Before.</p>" + BUTTON.format(href=href, label=label) + "<p>After.</p>"
    assert words(html) == "Before. After."


@pytest.mark.parametrize("html", [
    '<p>Read the rest <a href="https://www.theguardian.com/x">here</a>.</p>',
    f'<p>Read the rest <a href="{SITE}/another-post/">here</a> if you want.</p>',
    '<p>This piece appears in my book <a href="https://orbooks.com/catalog/we-had-it-coming/">We Had It Coming</a>.</p>',
    '<p>My new book is shipping out this week. <a href="https://orbooks.com/catalog/x/">Order it now</a>!</p>',
    "<p>by Sean T. Collins</p>",
    "<p>Sean T. Collins is a critic who has written for The New York Times.</p>",
    "<p>Should you subscribe now to the idea that nothing will ever change?</p>",
    "<p>Thanks as always to those supporting my work here.</p>",
    "<blockquote><p>Subscribe now</p></blockquote>",  # someone else's words
])
def test_writing_stays(html):
    assert BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split()[0] in words(html)
    assert words(html) == " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())


def test_links_in_kept_lines_stay():
    html = '<p>Read the rest <a href="https://www.theguardian.com/x">here</a>.</p>'
    assert processed(html).a["href"] == "https://www.theguardian.com/x"


def test_sales_word_in_a_longer_paragraph_keeps_the_words_and_drops_the_portal_link():
    soup = processed(f'<p>Thank you for reading. <a href="{SITE}/#/portal">Subscribe here</a> if you can spare it.</p>')
    assert soup.find("a") is None
    assert "Thank you for reading. Subscribe here if you can spare it." in " ".join(soup.get_text(" ").split())


def test_removal_leaves_no_doubled_or_dangling_rules():
    assert str(processed("<p>A</p><hr><p>Subscribe now</p><hr><p>B</p>")).count("<hr") == 1
    assert "<hr" not in str(processed("<p>A</p><hr><p>Subscribe now</p>"))
    assert "<hr" not in str(processed("<p>Subscribe now</p><hr><p>A</p>"))
    assert str(processed("<p>A</p><hr><p>B</p><hr><p>C</p>")).count("<hr") == 2  # untouched where nothing was removed


def test_removals_are_reported_and_the_book_is_valid(build_book, tmp_path):
    html = (f'<p>Start.</p><p><a href="{SITE}/#/portal/signup?">Get 20% off for 1 year</a></p>'
            + BUTTON.format(href=f"{SITE}/come-on-man/", label="OK FINE")
            + "<p>Subscribe to read the rest.</p><p>End.</p>")
    book = build_book(post(GhostPlatform().normalize_html(html)), processor="hellworld")
    details = sorted(p.detail for p in book.result.problems if p.kind == "call-to-action")
    assert details == ['button removed: "OK FINE"', 'sales line removed: "Get 20% off for 1 year"',
                       'sales line removed: "Subscribe to read the rest."']
    urls = {p.url for p in book.result.problems if p.kind == "call-to-action"}
    assert f"{SITE}/come-on-man/" in urls
    chapter = book.chapter()
    assert "Start." in chapter and "End." in chapter
    assert "data-call-to-action" not in chapter and "OK FINE" not in chapter
    assert_valid_epub(book.path)


def test_contents_entries_are_plain_titles():
    post_ = Post(slug="p", title="A Title", html="", authors=[])
    assert get_processor("hellworld").display_title(post_) == "A Title"
