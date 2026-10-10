"""Website calls-to-action in books: banners, buttons, subscribe/portal links (#20)."""
from __future__ import annotations

import io
import re

import pytest
from bs4 import BeautifulSoup
from PIL import Image

import build_epub
import phoenix_ebook.processors  # noqa: F401 — registers flaminghydra
from conftest import assert_valid_epub, ok, png, post
from phoenix_ebook.models import Post
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.processors.base import get_processor

SUB = "https://flaminghydra.com/subscribe"
LOGO = "https://storage.ghost.io/c/x/content/images/2025/07/High-Res-FH-Logo-Light-BG.gif"

# The real trailing block (end of "No, We Definitely Knew How Bad It Would Be"), trimmed.
TRAILING_BLOCK = (
    '<hr><h3 id="if-you-love-this-free-post">If you love this free post subscribe, starting at just $3/month, to</h3>'
    '<figure class="kg-card kg-image-card kg-card-hascaption"><a href="' + SUB + '"><img src="' + LOGO + '" '
    'class="kg-image" alt="" width="2000" height="488"></a><figcaption><a href="' + SUB + '" rel="noreferrer">'
    '<i><b><strong>ENJOY A THOUGHTPROVOKING NEWSLETTER DAILY.</strong></b></i></a></figcaption></figure><p></p>'
)
BUTTON = '<div class="kg-card kg-button-card kg-align-center"><a href="{href}" class="kg-btn kg-btn-accent">{label}</a></div>'


def processed(html: str, processor: str = "flaminghydra") -> str:
    html = GhostPlatform().normalize_html(html)
    soup = BeautifulSoup(html, "html.parser")
    get_processor(processor).clean(soup, Post(slug="p", title="t", html=html))
    return str(soup)


def animated_gif() -> bytes:
    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "green")]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()


# ---------------------------------------------------------------- trailing banners

def test_trailing_banner_block_removed_entirely():
    out = processed("<p>Last real paragraph.</p>" + TRAILING_BLOCK)
    assert out == "<p>Last real paragraph.</p>"


def test_why_not_subscribe_variant_removed():
    html = ('<p>End.</p><hr><figure class="kg-card kg-image-card kg-card-hascaption"><a href="' + SUB + '">'
            '<img src="https://x/5131fe48.gif" alt=""></a><figcaption><a href="' + SUB + '">Why Not Subscribe?</a>'
            '</figcaption></figure>')
    assert processed(html) == "<p>End.</p>"


def test_mid_post_banner_is_kept_and_marked():
    html = ('<p>Save the time it will take you to read this post and subscribe.</p>'
            '<figure><a href="' + SUB + '"><img src="https://x/bear.png" alt=""></a></figure>'
            '<p>The Holiday Bear has a message for you, and it goes on for a while.</p>')
    out = processed(html)
    assert "bear.png" in out and "data-call-to-action-banner" in out


def test_banner_followed_by_notes_is_not_trailing():
    html = ('<p>Body.<sup class="footnote-ref"><a href="#fn1" id="fnref1">[1]</a></sup></p>'
            '<figure><a href="' + SUB + '"><img src="https://x/b.gif" alt=""></a></figure>'
            '<hr class="footnotes-sep"><section class="footnotes"><ol class="footnotes-list">'
            '<li id="fn1" class="footnote-item"><p>Note. <a href="#fnref1" class="footnote-backref">↩︎</a></p></li>'
            '</ol></section>')
    assert "b.gif" in processed(html)


def test_image_linking_elsewhere_is_untouched():
    html = '<p>End.</p><figure><a href="https://bsky.app/profile/flaminghydra"><img src="https://x/sky.png" alt="Sky"></a></figure>'
    assert "sky.png" in processed(html)


# ---------------------------------------------------------------- buttons

def test_button_card_becomes_canonical_button():
    out = GhostPlatform().normalize_html(BUTTON.format(href="https://example.org/book", label="READ THE BOOK"))
    assert out == '<p class="button"><a href="https://example.org/book">READ THE BOOK</a></p>'


@pytest.mark.parametrize("href, label", [
    ("#/portal/support", "BUY FLAMING HYDRA A SLICE"),                      # Ghost-wide: dead portal link
    ("https://bsky.app/intent/compose?text=FLAMING%20HYDRA", "SHARE THIS POST ON BLUESKY!"),
    ("https://shop.flaminghydra.com/", "VISIT THE FLAMING HYDRA SUPERSTORE"),
    ("https://flaminghydra.com/subscribe", "SUBSCRIBE"),
])
def test_call_to_action_buttons_removed_whole(href, label):
    out = processed("<p>Before.</p>" + BUTTON.format(href=href, label=label) + "<p>After.</p>")
    assert out == "<p>Before.</p><p>After.</p>"


def test_other_buttons_kept():
    out = processed(BUTTON.format(href="https://example.org/book", label="READ THE BOOK"))
    assert '<p class="button"><a href="https://example.org/book">READ THE BOOK</a></p>' == out


# ---------------------------------------------------------------- text links

@pytest.mark.parametrize("href", ["/#/portal/signup", "https://flaminghydra.com/subscribe", "/subscribe"])
def test_links_in_sentences_unwrapped_words_kept(href):
    out = processed(f'<p>If you love it, why not <a href="{href}">subscribe or donate</a>?</p>')
    text = BeautifulSoup(out, "html.parser").get_text()
    assert text == "If you love it, why not subscribe or donate?"
    assert "<a " not in out


def test_ordinary_links_untouched():
    html = '<p>See <a href="https://example.org/subscribe-to-reason">this essay</a> and <a href="/about">about</a>.</p>'
    assert processed(html) == html


# ---------------------------------------------------------------- generic processor

def test_generic_processor_keeps_site_rules_off_but_ghost_portal_handling_on():
    html = '<p>a <a href="#/portal/signup">sign up</a></p>' + TRAILING_BLOCK
    out = processed(html, processor="generic")
    assert "High-Res-FH-Logo" in out                     # FH banner rule not applied
    assert 'href="#/portal' not in out                   # Ghost portal handling still applied


# ---------------------------------------------------------------- report and the built book

def test_report_lists_unlinked_paragraphs_and_kept_banners(build_book):
    html = ('<p>Why not <a href="https://flaminghydra.com/subscribe">subscribe</a> or '
            '<a href="#/portal/support">donate</a>?</p>'
            '<figure><a href="' + SUB + '"><img src="https://img.test/bear.png" alt="A bear"></a>'
            '<figcaption>The bear</figcaption></figure><p>More of the post.</p>')
    book = build_book(post(GhostPlatform().normalize_html(html)), {"https://img.test/bear.png": [ok(png())]},
                      processor="flaminghydra")
    cta = [p for p in book.result.problems if p.kind == "call-to-action"]
    assert len(cta) == 2  # one per paragraph (two links), one for the kept banner
    assert 'words kept: "Why not subscribe or donate?"' in cta[0].detail
    assert "promotional image kept" in cta[1].detail and "The bear" in cta[1].detail
    chapter = book.chapter()
    assert "data-call-to-action" not in chapter and "Why not subscribe or donate?" in chapter


def test_removed_banner_is_never_downloaded_and_hazard_is_none(build_book):
    html = "<p>Last real paragraph.</p>" + TRAILING_BLOCK
    book = build_book(post(GhostPlatform().normalize_html(html)), {LOGO: [ok(animated_gif(), "image/gif")]},
                      processor="flaminghydra")
    assert LOGO not in book.session.calls and not book.image_names()
    assert re.findall(r'schema:accessibilityHazard">([^<]*)', book.text("content.opf")) == ["none"]
    assert_valid_epub(book.path)


def test_cli_summary_line(capsys):
    from phoenix_ebook.models import BuildProblem
    build_epub._report_problems([BuildProblem("call-to-action", "p", SUB, 'link removed, words kept: "x"')])
    assert "1 website call-to-action (subscribe/support) was removed, unlinked or kept" in capsys.readouterr().err


def test_portal_link_around_an_image_is_described(build_book):
    html = ('<figure><a href="#/portal/"><img src="https://img.test/free.png" alt=""></a>'
            '<figcaption>Free posts! Click here!</figcaption></figure><p>More.</p>')
    book = build_book(post(GhostPlatform().normalize_html(html)), {"https://img.test/free.png": [ok(png())]},
                      processor="flaminghydra")
    [cta] = [p for p in book.result.problems if p.kind == "call-to-action"]
    assert cta.detail == 'link removed from an image (image kept): "Free posts! Click here!"'


# ---------------------------------------------------------------- closing appeals in text (#30)

# The real endings of three posts (football-in-palestine, gazans-living-in-egypt,
# antisemitism-and-the-unspeakable), after a last paragraph of the essay.
LAST = "<p>The essay's last real paragraph.</p>"
CLOSING_APPEALS = [
    '<hr><p><em>If  you enjoyed this free post, </em><a href="https://flaminghydra.com/subscribe" rel="noreferrer">'
    '<strong><em>subscribe</em></strong></a><em> or<strong> </strong></em><a href="https://flaminghydra.com/donate/" '
    'rel="noreferrer"><strong><em>donate</em></strong></a><em> to Flaming Hydra and receive incandescent essays, '
    'comics, criticism and more from us each weekday.</em></p>',
    '<hr><p>If you loved this story, send it to a friend, recommend it on socials... and '
    '<a href="https://flaminghydra.com/subscribe" rel="noreferrer"><strong><em>Subscribe to Flaming Hydra</em></strong>'
    '</a><strong><em>.</em></strong></p>',
    '<hr><p><strong><em>If you enjoyed this free post, help support the journalist-owned press. </em></strong>'
    '<a href="https://flaminghydra.com/subscribe" rel="noreferrer"><strong><em>Subscribe</em></strong></a>'
    '<strong><em> to Flaming Hydra.</em></strong></p>',
]


@pytest.mark.parametrize("ending", CLOSING_APPEALS)
def test_closing_appeal_after_the_last_rule_is_removed(ending):
    out = processed(LAST + ending + "<p></p>")
    soup = BeautifulSoup(out, "html.parser")
    assert [t.name for t in soup.find_all(True) if t.name != "span"] == ["p"]
    assert soup.get_text() == "The essay's last real paragraph."
    assert soup.find("span", attrs={"data-call-to-action-removed": True})


def test_final_section_without_an_appeal_is_kept():
    html = LAST + '<hr/><p>A coda, with <a href="https://example.org/">a link</a>.</p>'
    assert processed(html) == html


def test_long_final_section_is_kept_even_with_a_subscribe_link():
    coda = " ".join(["word"] * 160)
    out = processed(LAST + f'<hr><p>{coda} <a href="https://flaminghydra.com/subscribe">subscribe</a></p>')
    assert coda in out and "<hr" in out  # left alone; the link alone is unwrapped and reported


def test_only_the_last_section_counts():
    html = (LAST + '<hr><p>Middle, <a href="https://flaminghydra.com/subscribe">subscribe</a> here.</p>'
            '<hr><p>The real ending.</p>')
    text = BeautifulSoup(processed(html), "html.parser").get_text()
    assert "The real ending." in text and "Middle, subscribe here." in text


def test_donate_links_mid_post_are_unlinked_like_subscribe_links():
    out = processed('<p>Please <a href="https://flaminghydra.com/donate/">donate</a> today.</p><p>More.</p>')
    assert "<a " not in out and "Please donate today." in BeautifulSoup(out, "html.parser").get_text()


def test_removed_closing_appeal_is_reported(build_book):
    book = build_book(post(GhostPlatform().normalize_html(LAST + CLOSING_APPEALS[0])), processor="flaminghydra")
    (cta,) = [p for p in book.result.problems if p.kind == "call-to-action"]
    assert cta.detail.startswith('closing appeal removed: "If you enjoyed this free post, subscribe or donate')
    assert cta.url == "https://flaminghydra.com/subscribe"
    chapter = book.chapter()
    assert "If you enjoyed" not in chapter and "data-call-to-action" not in chapter and "<hr" not in chapter


# ---------------------------------------------------------------- invitations to the comments

COMMENTS = "https://flaminghydra.com/issue-TKTK#comments"
INVITATIONS = [
    # after a rule (most posts)
    '<hr><p><a href="' + COMMENTS + '" rel="noreferrer">You may Shred in the Comments Section</a></p>',
    # no rule ("The Insult That Made a Man Make Another Man")
    '<p><a href="' + COMMENTS + '" rel="noreferrer">Make something of yourself in the Comments Section</a></p>',
    # split across links, with stray bold space ("Lost Cat", "Remembering Linda Yaccarino's Career")
    '<p><a href="' + COMMENTS + '"><strong>Approach this piece </strong></a><a href="' + COMMENTS + '">'
    '<strong>in the Comments Section </strong></a><strong> </strong></p>',
    # after a rule, partly outside the link ("Podcast: How to Fix AI")
    '<hr><p>Get out of the doom loop and <a href="' + COMMENTS + '">discuss in the comments</a> !</p>',
]


@pytest.mark.parametrize("invitation", INVITATIONS)
def test_invitation_to_the_comments_is_removed(invitation):
    assert processed(LAST + invitation + "<p></p>") == LAST


def test_invitation_above_the_share_buttons_is_removed_and_what_follows_kept():
    out = processed(LAST + INVITATIONS[1] + '<figure><img src="https://example.org/a.png" alt="A still."></figure>')
    assert "Comments Section" not in out and 'alt="A still."' in out


def test_sentence_mentioning_the_comments_is_kept():
    html = '<p>As a reader put it in <a href="' + COMMENTS + '">the comments</a>, the ending was earned.</p>' + LAST
    assert processed(html) == html


def test_long_paragraph_after_a_rule_mentioning_the_comments_is_kept():
    html = LAST + "<hr/><p>" + " ".join(["word"] * 40) + ' <a href="' + COMMENTS + '">comments</a></p>'
    assert processed(html) == html
