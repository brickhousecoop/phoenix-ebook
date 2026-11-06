"""WordPress full-text feed platform (#49): fetching by slug, the secret feed address, WordPress block HTML,
and the blog settings it relies on (time zone, categories left out)."""
from __future__ import annotations

import pytest
import requests

from conftest import FakeResponse, FakeSession, post
from phoenix_ebook.errors import AuthError, InvalidBookSpec, PostNotFound, SiteFileError, SourceUnreachable
from phoenix_ebook.platforms.base import get_platform
from phoenix_ebook.platforms.wordpress_feed import WordPressFeedPlatform
from phoenix_ebook.processors.base import get_processor, select_processor
from phoenix_ebook.processors.rules import load_site_rules

SITE = "https://blog.example"
FEED = "https://blog.example/feed/0123456789abcdef0123456789abcdef?key=s3cr3t-key"
FEED_FOR = lambda slug: f"https://blog.example/feed/0123456789abcdef0123456789abcdef?key=s3cr3t-key&name={slug}"


def rss(*items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>Blog</title><link>{SITE}</link>{''.join(items)}</channel></rss>""".encode()


def item(slug="a-post", *, creators=("Ann Writer",), categories=("Basketball", "nba"), date="Fri, 06 Nov 2026 02:30:00 +0000",
         html="<p>Body.</p>", lead="https://img.example/2026/11/lead.jpg?w=564") -> str:
    return (f"<item><title>A  Post</title><link>{SITE}/{slug}</link><pubDate>{date}</pubDate>"
            + "".join(f"<dc:creator><![CDATA[{c}]]></dc:creator>" for c in creators)
            + "".join(f"<category><![CDATA[{c}]]></category>" for c in categories)
            + f"<guid>{SITE}/?p=1</guid><description><![CDATA[Excerpt.]]></description>"
            + f"<content:encoded><![CDATA[{html}]]></content:encoded>"
            + (f'<media:content url="{lead}" medium="image"/>' if lead else "") + "</item>")


def fetch(response, slug="a-post", secret=FEED):
    session = FakeSession({FEED_FOR(slug): [response]})
    return WordPressFeedPlatform().fetch_post(session, SITE, secret, slug), session


# ---------------------------------------------------------------- fetching

def test_fetches_one_post_by_name_from_the_private_feed():
    got, session = fetch(FakeResponse(200, rss(item()), "application/rss+xml"))
    assert [url for url, _ in session.log] == [FEED_FOR("a-post")]
    assert got.slug == "a-post" and got.title == "A Post"
    assert [a.name for a in got.authors] == ["Ann Writer"] and got.authors[0].url is None
    assert got.categories == ["Basketball", "nba"]
    assert got.published_at == "2026-11-06T02:30:00+00:00"
    assert got.feature_image == "https://img.example/2026/11/lead.jpg?w=1600"
    assert got.html == "<p>Body.</p>"


def test_every_co_author_in_feed_order():
    got, _ = fetch(FakeResponse(200, rss(item(creators=("Ann Writer", "Bo Second", "Cy Third")))))
    assert [a.name for a in got.authors] == ["Ann Writer", "Bo Second", "Cy Third"]


def test_no_lead_image():
    got, _ = fetch(FakeResponse(200, rss(item(lead=None))))
    assert got.feature_image is None


def test_missing_post_is_post_not_found():
    with pytest.raises(PostNotFound) as err:
        fetch(FakeResponse(404, b"<html>not found</html>", "text/html"), slug="nope")
    assert err.value.slugs == ["nope"] and err.value.site == "blog.example"


def test_feed_that_ignores_name_is_not_a_post():
    with pytest.raises(PostNotFound):
        fetch(FakeResponse(200, rss(item("one"), item("two"))))


@pytest.mark.parametrize("status", [401, 403])
def test_refused_feed_says_to_store_it_again(status):
    with pytest.raises(AuthError) as err:
        fetch(FakeResponse(status, b"no"))
    assert "--set-secret --platform wordpress-feed" in str(err.value)


def test_not_a_feed():
    with pytest.raises(SourceUnreachable):
        fetch(FakeResponse(200, b"<html><body>Sign in</body></html>", "text/html"))


def test_unreachable():
    with pytest.raises(SourceUnreachable) as err:
        fetch(requests.ConnectionError(f"failed to connect to {FEED_FOR('a-post')}"))
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_feed_on_another_site_is_refused_before_any_request():
    session = FakeSession({})
    with pytest.raises(AuthError) as err:
        WordPressFeedPlatform().fetch_post(session, SITE, "https://other.example/feed/x?key=k", "a-post")
    assert "different site (other.example)" in str(err.value) and session.log == []


def test_www_is_the_same_site():
    session = FakeSession({"https://www.blog.example/feed/x?key=k&name=a-post": [FakeResponse(200, rss(item()))]})
    assert WordPressFeedPlatform().fetch_post(session, SITE, "https://www.blog.example/feed/x?key=k", "a-post")


@pytest.mark.parametrize("response", [
    FakeResponse(404, b""), FakeResponse(401, b""), FakeResponse(500, b""), FakeResponse(200, b"<html>"),
    FakeResponse(200, b"<feed/>"), requests.Timeout(FEED),
])
def test_the_feed_address_never_appears_in_an_error(response):
    with pytest.raises(Exception) as err:
        fetch(response)
    for secret in ("s3cr3t-key", "0123456789abcdef0123456789abcdef"):
        assert secret not in str(err.value) and secret not in repr(err.value.__cause__ or "")


def test_registered():
    assert isinstance(get_platform("wordpress-feed"), WordPressFeedPlatform)
    assert WordPressFeedPlatform.dates_in_utc


# ---------------------------------------------------------------- WordPress blocks -> canonical HTML

norm = WordPressFeedPlatform().normalize_html


def test_paragraph_classes_go():
    assert norm('<p class="wp-block-paragraph has-text-align-center">Hi</p>') == "<p>Hi</p>"


def test_image_block():
    html = norm('<figure class="wp-block-image size-large"><img fetchpriority="high" width="993" '
                'src="https://img.example/a.png?w=993" alt="A chart" class="wp-image-14033" '
                'srcset="https://img.example/a.png 1214w" sizes="(max-width: 993px) 100vw"/>'
                '<figcaption class="wp-element-caption">Chart: <em>Me</em></figcaption></figure>')
    assert html == ('<figure><img alt="A chart" src="https://img.example/a.png?w=993" width="993"/>'
                    "<figcaption>Chart: <em>Me</em></figcaption></figure>")


def test_youtube_embed_becomes_a_video_card_with_its_caption():
    html = norm('<figure class="wp-block-embed is-type-video is-provider-youtube wp-block-embed-youtube">'
                '<div class="wp-block-embed__wrapper"><iframe title="A Game" src="https://www.youtube.com/embed/'
                'eJOkrA3VB7c?feature=oembed"></iframe></div><figcaption class="wp-element-caption">Look</figcaption></figure>')
    assert 'data-card="video"' in html and "https://www.youtube.com/watch?v=eJOkrA3VB7c" in html
    assert html.endswith("<figcaption>Look</figcaption></figure>") and "wp-" not in html


def test_tweet_embed_becomes_a_post_card_without_scripts():
    html = norm('<figure class="wp-block-embed-twitter wp-block-embed is-type-rich is-provider-twitter">'
                '<div class="wp-block-embed__wrapper"><blockquote class="twitter-tweet" data-width="550">'
                '<p lang="en" dir="ltr">Hello</p>&mdash; Someone (@some) <a href="https://twitter.com/some/status/'
                '1310703645160288256?ref_src=x">September 28, 2020</a></blockquote>'
                '<script async src="https://platform.twitter.com/widgets.js"></script></div></figure>')
    assert 'data-card="post"' in html and "<script" not in html and "Hello" in html


def test_instagram_embed_becomes_a_link_to_the_post():
    html = norm('<figure class="wp-block-embed is-provider-instagram wp-block-embed-instagram"><div class='
                '"wp-block-embed__wrapper"><blockquote class="instagram-media" data-instgrm-permalink='
                '"https://www.instagram.com/p/CYnO7cDqbdj/?utm_source=ig_embed"><div><p><a href="https://www.'
                'instagram.com/p/CYnO7cDqbdj/">A post shared by Novak Djokovic (@djokernole)</a></p></div>'
                '</blockquote><script src="//www.instagram.com/embed.js"></script></div></figure>')
    assert 'data-card="post"' in html and 'href="https://www.instagram.com/p/CYnO7cDqbdj/"' in html
    assert "Novak Djokovic (@djokernole)" in html and "<script" not in html


def test_unrecognised_embed_is_a_reported_card():
    html = norm('<figure class="wp-block-embed is-provider-audioboom wp-block-embed-audioboom"><div class='
                '"wp-block-embed__wrapper"><iframe title="Audioboom player" src="https://embeds.audioboom.com/'
                'posts/1/embed/v4"></iframe></div></figure>')
    assert "data-embed-unknown" in html and "wp-" not in html


def test_embed_with_only_an_address():
    html = norm('<figure class="wp-block-embed is-provider-twitter wp-block-embed-twitter"><div class='
                '"wp-block-embed__wrapper">\nhttps://twitter.com/some/status/1\n</div></figure>')
    assert 'data-embed-unknown="https://twitter.com/some/status/1"' in html


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_self_hosted_media_becomes_a_card(kind):
    html = norm(f'<figure class="wp-block-{kind}"><{kind} controls src="https://img.example/clip.mp4"></{kind}>'
                f'<figcaption class="wp-element-caption">Clip</figcaption></figure>')
    assert f'data-card="{kind}"' in html and 'href="https://img.example/clip.mp4"' in html
    assert "MP4" in html and "<figcaption>Clip</figcaption>" in html and f"<{kind} " not in html


def test_quote_pullquote_separator_list():
    html = norm('<blockquote class="wp-block-quote is-layout-flow wp-block-quote-is-layout-flow"><p>Q</p></blockquote>'
                '<figure class="wp-block-pullquote"><blockquote><p>Pulled</p><cite>Who</cite></blockquote></figure>'
                '<hr class="wp-block-separator has-alpha-channel-opacity"/>'
                '<ul class="wp-block-list"><li>one</li></ul>')
    assert html == ('<blockquote><p>Q</p></blockquote><blockquote class="pullquote"><p>Pulled</p><cite>Who</cite>'
                    '</blockquote><hr/><ul><li>one</li></ul>')


def test_gallery_becomes_its_images():
    html = norm('<figure class="wp-block-gallery has-nested-images columns-default is-layout-flex '
                'wp-block-gallery-is-layout-flex"><figure class="wp-block-image size-large"><img src="a.jpg" alt="A"/>'
                '</figure><figure class="wp-block-image"><img src="b.jpg" alt="B"/></figure>'
                '<figcaption class="blocks-gallery-caption wp-element-caption">Both</figcaption></figure>')
    assert html == '<figure><img alt="A" src="a.jpg"/></figure><figure><img alt="B" src="b.jpg"/></figure><p>Both</p>'


def test_layout_blocks_are_unwrapped_and_spacers_dropped():
    html = norm('<div class="wp-block-media-text is-stacked-on-mobile"><figure class="wp-block-media-text__media">'
                '<img src="a.jpg" alt="A"/></figure><div class="wp-block-media-text__content"><p>Text</p></div></div>'
                '<div style="height:20px" aria-hidden="true" class="wp-block-spacer"></div>')
    assert html == '<figure><img alt="A" src="a.jpg"/></figure><p>Text</p>'


def test_button_block_becomes_a_canonical_button():
    html = norm('<div class="wp-block-buttons"><div class="wp-block-button"><a class="wp-block-button__link '
                'wp-element-button" href="https://blog.example/subscribe">Subscribe</a></div></div>')
    assert html == '<p class="button"><a href="https://blog.example/subscribe">Subscribe</a></p>'


def test_unknown_block_keeps_its_contents_and_is_marked():
    html = norm('<div class="wp-block-lede-paywall-teaser"><p class="wp-block-paragraph">Kept</p></div>')
    assert html == '<span data-markup-unknown="wp-block-lede-paywall-teaser"></span><p>Kept</p>'


def test_emoji_pictures_become_emoji():
    html = norm('<p class="wp-block-paragraph">Look <img draggable="false" role="img" class="emoji" alt="👇" '
                'src="https://s.w.org/images/core/emoji/17.0.2/72x72/1f447.png"/></p>')
    assert html == "<p>Look 👇</p>"


def test_html_without_wordpress_markup_is_untouched():
    assert norm("<p>Plain <em>text</em></p>") == "<p>Plain <em>text</em></p>"


# ---------------------------------------------------------------- blog settings and the build

def test_defector_uses_its_file():
    assert select_processor("wordpress-feed", "defector.com", "generic") == "defector"
    rules = get_processor("defector").rules
    assert str(rules.timezone) == "America/New_York" and rules.skip_categories == ("podcasts", "crosswords")


def test_bad_timezone_stops_that_blog(tmp_path):
    (tmp_path / "blog.toml").write_text('timezone = "Mars/Olympus"')
    with pytest.raises(SiteFileError, match="time zone name"):
        load_site_rules("blog", tmp_path)


def test_skip_categories_must_be_words(tmp_path):
    (tmp_path / "blog.toml").write_text("skip_categories = [1]")
    with pytest.raises(SiteFileError, match="skip_categories"):
        load_site_rules("blog", tmp_path)


def test_date_shown_in_the_blogs_time_zone(build_book):
    late = post(published_at="2026-11-06T02:30:00+00:00")  # 9.30 pm on the 5th in New York
    built = build_book(late, processor="defector", platform=WordPressFeedPlatform())
    assert "05 Nov 2026" in built.chapter() and "dates-in-utc" not in {p.kind for p in built.result.problems}


def test_utc_dates_reported_once_when_the_blog_has_no_time_zone(build_book):
    posts = [post(slug="a", published_at="2026-11-06T02:30:00+00:00"), post(slug="b")]
    built = build_book(posts, platform=WordPressFeedPlatform())
    assert "06 Nov 2026" in built.chapter("a")
    assert [p.kind for p in built.result.problems].count("dates-in-utc") == 1


def test_ghost_dates_are_not_reported(build_book):
    built = build_book(post(published_at="2026-11-06T02:30:00+00:00"), platform=get_platform("ghost"))
    assert "dates-in-utc" not in {p.kind for p in built.result.problems}


def test_posts_in_skipped_categories_are_left_out_and_reported(build_book):
    posts = [post(slug="game", title="The Crossword, Oct. 19", categories=["Crosswords"]),
             post(slug="essay", categories=["NBA", "podcastsish"])]
    built = build_book(posts, processor="defector")
    assert "EPUB/game.xhtml" not in built.zip.namelist() and "EPUB/essay.xhtml" in built.zip.namelist()
    left_out = [p for p in built.result.problems if p.kind == "post-left-out"]
    assert len(left_out) == 1 and left_out[0].post_slug == "game"
    assert '"The Crossword, Oct. 19" is in Crosswords' in left_out[0].detail


def test_cli_warning_without_an_address(capsys):
    import build_epub
    from phoenix_ebook.models import BuildProblem
    build_epub._report_problems([BuildProblem("post-left-out", "game", None, '"G" is in Crosswords, which …')])
    err = capsys.readouterr().err
    assert 'warning: [game] "G" is in Crosswords' in err and "None" not in err
    assert "1 post was left out" in err


def test_a_book_of_only_skipped_posts_stops(build_book):
    with pytest.raises(InvalidBookSpec, match="every post was left out.*podcasts"):
        build_book(post(categories=["podcasts"]), processor="defector")


def test_unknown_markup_reported_once_per_kind_per_book(build_book):
    marker = '<span data-markup-unknown="wp-block-x"></span><p>Kept</p>'
    built = build_book([post(marker, slug="a"), post(marker, slug="b")])
    unknown = [p for p in built.result.problems if p.kind == "markup-unknown"]
    assert len(unknown) == 1 and unknown[0].post_slug == "a" and "wp-block-x" in unknown[0].detail
    assert "data-markup-unknown" not in built.chapter("a") and "Kept" in built.chapter("b")
