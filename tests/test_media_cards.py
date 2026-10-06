"""Ghost media cards and embeds become link cards (#21)."""
from __future__ import annotations

import json
import re

import pytest
from bs4 import BeautifulSoup

import build_epub
from conftest import FakeResponse, assert_valid_epub, jpeg, ok, png, post
from phoenix_ebook.models import BuildProblem
from phoenix_ebook.platforms.ghost import GhostPlatform

STORAGE = "https://storage.ghost.io/c/11/a3/x/content"

# Real markup from flaminghydra.com posts, trimmed (player controls shortened, scripts kept).
YOUTUBE = ('<figure class="kg-card kg-embed-card"><iframe allow="autoplay" allowfullscreen="" frameborder="0" '
           'height="150" src="https://www.youtube.com/embed/kw0YhiqOPFg?feature=oembed" '
           'title="GODZILLA MINUS ZERO | Official 1.43:1 Trailer | Filmed For IMAX" width="200"></iframe></figure>')
VIMEO = ('<figure class="kg-card kg-embed-card kg-card-hascaption"><iframe frameborder="0" height="480" '
         'src="https://player.vimeo.com/video/148990643?app_id=122963" '
         'title=\'Bill Murray as "Nick The Lounge Singer" sings "Star Wars"\' width="640"></iframe>'
         '<figcaption><p><span style="white-space: pre-wrap;">May all your ball wars be star wars</span></p>'
         '</figcaption></figure>')
SPOTIFY = ('<figure class="kg-card kg-embed-card"><iframe height="152" '
           'src="https://open.spotify.com/embed/episode/1NjA8j3U9hcqVsywZ8vY0v?si=x&amp;utm_source=oembed" '
           'title=\'Spotify Embed: Chapter 15: "Chowder"\' width="100%"></iframe></figure>')
TALLY = ('<figure class="kg-card kg-embed-card"><iframe frameborder="0" height="100%" '
         'src="https://tally.so/embed/VLjVLN?alignLeft=1&amp;hideTitle=1" title="Tally Forms" width="100%"></iframe>'
         '<script>var d=document,w="https://tally.so/widgets/embed.js";</script></figure>')
TALLY_LOOSE = ('<iframe data-tally-src="https://tally.so/embed/3Xq0Gg?alignLeft=1" frameborder="0" height="423" '
               'title="Reader Poll:What Publishing Format Would Best Suit You?" width="100%"></iframe>'
               '<script>var d=document;</script>')
DAILYMOTION = ('<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;"> <iframe '
               'allowfullscreen="" src="https://www.dailymotion.com/embed/video/xhgb8j_in-search-of-moebius" '
               'title="Dailymotion Video Player" width="100%"> </iframe> </div>')
TWEET = ('<figure class="kg-card kg-embed-card"><blockquote class="twitter-tweet"><p dir="ltr" lang="en">"things you '
         'people wouldn\'t believe…" <a href="https://t.co/dB9CHBoWIv">pic.twitter.com/dB9CHBoWIv</a></p>— '
         'clipsclicks (@clips4clicks) <a href="https://twitter.com/clips4clicks/status/1794387135623200864?ref_src='
         'twsrc%5Etfw">May 25, 2024</a></blockquote>\n<script async="" charset="utf-8" '
         'src="https://platform.twitter.com/widgets.js"></script></figure>')
BLUESKY = ('<figure class="kg-card kg-embed-card"><blockquote class="bluesky-embed" data-bluesky-uri="at://did:plc:gx6/'
           'app.bsky.feed.post/3lnjbb3bxkk2k"><p lang="en">because the economy is a mess.\n\nbecause the work never '
           'ends.\nstarting 5/7</p>— <a href="https://bsky.app/profile/did:plc:gx6?ref_src=embed">open mike eagle '
           '(@mike-eagle.bsky.social)</a> <a href="https://bsky.app/profile/did:plc:gx6/post/3lnjbb3bxkk2k?ref_src='
           'embed">2025-04-23T22:41:41.180Z</a></blockquote><script async="" '
           'src="https://embed.bsky.app/static/embed.js"></script></figure>')
TIKTOK_URL = "https://www.tiktok.com/@carrottoplive/video/7222019045850123563"
TIKTOK = ('<figure class="kg-card kg-embed-card"><blockquote cite="' + TIKTOK_URL + '" class="tiktok-embed" '
          'data-video-id="7222019045850123563" style="max-width:605px;"> <section> <a href="https://www.tiktok.com/'
          '@carrottoplive?refer=embed" target="_blank" title="@carrottoplive">@carrottoplive</a> <p>I’ve been '
          'roasting <a href="https://www.tiktok.com/tag/wendys?refer=embed" title="wendys">#wendys</a> for decades'
          '</p> <a href="https://www.tiktok.com/music/original-sound-7222019006642408235?refer=embed" '
          'title="♬ original sound - Carrot Top">♬ original sound - Carrot Top</a> </section> </blockquote> '
          '<script async="" src="https://www.tiktok.com/embed.js"></script></figure>')
AUDIO = ('<div class="kg-card kg-audio-card"><img alt="audio-thumbnail" class="kg-audio-thumbnail kg-audio-hide" src=""/>'
         '<div class="kg-audio-thumbnail placeholder"><svg fill="none" height="24" width="24"><path d="M7.5 15"></path>'
         '</svg></div><div class="kg-audio-player-container"><audio preload="metadata" src="' + STORAGE +
         '/media/2026/04/FH-pod-0019-maria-dave-karpf.mp3"></audio><div class="kg-audio-title">PODCAST! Maria and Dave '
         'Karpf</div><div class="kg-audio-player"><button aria-label="Play audio" class="kg-audio-play-icon"><svg '
         'viewbox="0 0 24 24"><path d="M23"></path></svg></button><span class="kg-audio-current-time">0:00</span>'
         '<div class="kg-audio-time">/<span class="kg-audio-duration">2229.705261</span></div><input '
         'class="kg-audio-seek-slider" max="100" type="range" value="0"/></div></div></div>')
VIDEO_THUMB = STORAGE + "/media/2026/09/G-Minus-0-IMAX_thumb.jpg"
VIDEO = ('<figure class="kg-card kg-video-card kg-width-wide" data-kg-custom-thumbnail="" data-kg-thumbnail="'
         + VIDEO_THUMB + '">\n<div class="kg-video-container">\n<video height="1080" playsinline="" poster="data:image/'
         'gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" preload="metadata" src="' + STORAGE +
         '/media/2026/09/G-Minus-0-IMAX.mp4" width="1544"></video>\n<div class="kg-video-overlay">\n<button '
         'aria-label="Play video" class="kg-video-large-play-icon">\n<svg viewbox="0 0 24 24" '
         'xmlns="http://www.w3.org/2000/svg"><path d="M23"></path></svg>\n</button>\n</div>\n<span '
         'class="kg-video-duration">0:11</span>\n<input class="kg-video-seek-slider" max="100" type="range" value="0"/>'
         '\n</div>\n<figcaption><p>Eyemaxxing</p></figcaption></figure>')
BM_ICON = "https://assets.guim.co.uk/static/frontend/icons/homescreen/apple-touch-icon.svg"
BM_THUMB = "https://i.guim.co.uk/img/media/arsenal.jpg?width=1200"
BOOKMARK = ('<figure class="kg-card kg-bookmark-card"><a class="kg-bookmark-container" href="https://www.theguardian.com/'
            'books/black-arsenal"><div class="kg-bookmark-content"><div class="kg-bookmark-title">Black Arsenal: how the '
            'club set the pace</div><div class="kg-bookmark-description">A new book explores how the north London side '
            'became a gamechanger for Black culture and racial integration in</div><div class="kg-bookmark-metadata">'
            '<img alt="" class="kg-bookmark-icon" src="' + BM_ICON + '"/><span class="kg-bookmark-author">The Guardian'
            '</span><span class="kg-bookmark-publisher">Tim Adams</span></div></div><div class="kg-bookmark-thumbnail">'
            '<img alt="" onerror="this.style.display = \'none\'" src="' + BM_THUMB + '"/></div></a></figure>')
GALLERY = ('<figure class="kg-card kg-gallery-card kg-width-wide"><div class="kg-gallery-container"><div '
           'class="kg-gallery-row"><div class="kg-gallery-image"><img alt="Parade" height="30" src="https://img.test/'
           'g1.jpg" width="40"/></div><div class="kg-gallery-image"><img alt="Float" height="30" src="https://img.test/'
           'g2.jpg" width="40"/></div></div></div></figure>')

YT_MAXRES = "https://i.ytimg.com/vi/kw0YhiqOPFg/maxresdefault.jpg"
YT_MQ = "https://i.ytimg.com/vi/kw0YhiqOPFg/mqdefault.jpg"
VIMEO_OEMBED = "https://vimeo.com/api/oembed.json?url=https%3A%2F%2Fvimeo.com%2F148990643&width=1100"  # bigger thumbnail
SPOTIFY_OEMBED = "https://open.spotify.com/oembed?url=https%3A%2F%2Fopen.spotify.com%2Fepisode%2F1NjA8j3U9hcqVsywZ8vY0v"
TIKTOK_OEMBED = ("https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40carrottoplive%2Fvideo%2F"
                 "7222019045850123563")


def normalized(html: str):
    return BeautifulSoup(GhostPlatform().normalize_html(html), "html.parser")


def card_of(html: str):
    [card] = normalized(html).find_all("figure", class_="card")
    return card


def text(tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split()) if tag is not None else ""


def oembed(**fields) -> FakeResponse:
    return FakeResponse(200, json.dumps(fields).encode(), "application/json")


# ---------------------------------------------------------------- canonical cards, kind by kind

def test_youtube_embed_becomes_video_card_with_16_9_thumbnail():
    card = card_of(YOUTUBE)
    assert text(card.find(class_="label")) == "Video"
    link = card.find(class_="title").a
    assert link["href"] == "https://www.youtube.com/watch?v=kw0YhiqOPFg"
    assert link.get_text() == "GODZILLA MINUS ZERO | Official 1.43:1 Trailer | Filmed For IMAX"
    assert text(card.find(class_="source")) == "YouTube"
    thumb = card.find("img", class_="thumbnail")
    assert thumb["src"] == YT_MAXRES and thumb["data-fallback-src"] == YT_MQ  # both 16:9; hqdefault has bars
    assert "hqdefault" not in str(card) and card.find("iframe") is None


def test_vimeo_embed_keeps_caption_and_looks_up_thumbnail():
    card = card_of(VIMEO)
    assert card.find(class_="title").a["href"] == "https://vimeo.com/148990643"
    assert card.find("img")["data-thumbnail-lookup"] == VIMEO_OEMBED
    assert text(card.find("figcaption")) == "May all your ball wars be star wars"
    assert card.contents[-1].name == "figcaption"


def test_spotify_embed_becomes_audio_card():
    card = card_of(SPOTIFY)
    assert text(card.find(class_="label")) == "Audio"
    assert card.find(class_="title").a["href"] == "https://open.spotify.com/episode/1NjA8j3U9hcqVsywZ8vY0v"
    assert text(card.find(class_="title")) == 'Chapter 15: "Chowder"'
    assert card.find("img")["data-thumbnail-lookup"] == SPOTIFY_OEMBED


def test_audio_card_player_becomes_link_to_the_file():
    soup = normalized(AUDIO)
    assert not soup.find_all(["svg", "input", "button", "audio"])
    card = soup.find("figure", class_="card")
    assert text(card.find(class_="title")) == "PODCAST! Maria and Dave Karpf"
    assert card.find(class_="title").a["href"].endswith("/FH-pod-0019-maria-dave-karpf.mp3")
    assert text(card.find(class_="source")) == "MP3 audio · 37 min"
    assert card.find("img") is None


def test_video_card_player_becomes_link_with_thumbnail_and_caption():
    soup = normalized(VIDEO)
    assert not soup.find_all(["svg", "input", "button", "video"])
    card = soup.find("figure", class_="card")
    assert card.find(class_="title").a["href"].endswith("/G-Minus-0-IMAX.mp4")
    assert text(card.find(class_="source")) == "MP4 video · 0:11"
    assert card.find("img", class_="thumbnail")["src"] == VIDEO_THUMB
    assert text(card.find("figcaption")) == "Eyemaxxing"


def test_bookmark_card_drops_icon_keeps_thumbnail():
    card = card_of(BOOKMARK)
    assert text(card.find(class_="label")) == "Link"
    assert card.find(class_="title").a["href"] == "https://www.theguardian.com/books/black-arsenal"
    assert text(card.find(class_="description")).endswith("racial integration in…")  # cut off by Ghost
    assert text(card.find(class_="source")) == "Tim Adams · The Guardian"  # writer · site
    assert [img["src"] for img in card.find_all("img")] == [BM_THUMB]


def test_tweet_becomes_post_card_with_readable_date_and_media_link():
    card = card_of(TWEET)
    assert text(card.find(class_="label")) == "Post on X"
    assert text(card.blockquote) == '"things you people wouldn\'t believe…"'
    source = card.find(class_="source")
    assert text(source) == "clipsclicks (@clips4clicks) · 25 May 2024 · attached media: view on X"
    links = {a.get_text(): a["href"] for a in source.find_all("a")}
    assert links == {"25 May 2024": "https://twitter.com/clips4clicks/status/1794387135623200864",
                     "view on X": "https://t.co/dB9CHBoWIv"}
    assert card.find("script") is None


def test_bluesky_post_paragraphs_and_iso_timestamp():
    card = card_of(BLUESKY)
    assert text(card.find(class_="label")) == "Post on Bluesky"
    paragraphs = card.blockquote.find_all("p")
    assert [text(p) for p in paragraphs] == ["because the economy is a mess.", "because the work never ends. starting 5/7"]
    assert paragraphs[1].find("br") is not None
    source = card.find(class_="source")
    assert text(source) == "open mike eagle (@mike-eagle.bsky.social) · 23 Apr 2025"
    assert "2025-04-23T" not in str(card)
    assert source.a["href"] == "https://bsky.app/profile/did:plc:gx6/post/3lnjbb3bxkk2k"


def test_tiktok_becomes_video_card_with_lookup():
    card = card_of(TIKTOK)
    assert text(card.find(class_="label")) == "Video on TikTok"
    assert text(card.blockquote) == "I’ve been roasting #wendys for decades"
    assert card.blockquote.find("a") is None  # hashtag links dropped
    assert text(card.find(class_="source")) == "@carrottoplive · watch on TikTok"
    assert card.find(class_="source").a["href"] == TIKTOK_URL
    assert card.find("img")["data-thumbnail-lookup"] == TIKTOK_OEMBED
    assert "original sound" not in str(card)


def test_tally_forms_removed_with_a_marker():
    soup = normalized("<p>Vote below.</p>" + TALLY + "<p>After.</p>")
    assert soup.find("iframe") is None and soup.find("figure") is None
    assert soup.find(attrs={"data-embed-removed": True})["data-embed-removed"] == "Tally form"
    loose = normalized(TALLY_LOOSE).find(attrs={"data-embed-removed": True})
    assert loose["data-embed-removed"] == 'Tally form "Reader Poll:What Publishing Format Would Best Suit You?"'


def test_unknown_embed_pasted_in_html_card_becomes_generic_card():
    soup = normalized("<p>Watch:</p>" + DAILYMOTION)
    assert soup.find("iframe") is None and soup.find("div") is None  # the sizing wrapper goes too
    card = soup.find("figure", class_="card")
    assert text(card.find(class_="label")) == "Embedded content"
    assert card.find(class_="title").a["href"].startswith("https://www.dailymotion.com/embed/video/")
    assert text(card.find(class_="source")) == "dailymotion.com"
    assert card["data-embed-unknown"].startswith("https://www.dailymotion.com/")


def test_no_empty_figures_or_embed_markup_left():
    soup = normalized(YOUTUBE + VIMEO + SPOTIFY + TWEET + BLUESKY + TIKTOK + AUDIO + VIDEO + BOOKMARK)
    assert not soup.find_all(["iframe", "script", "svg", "input", "button", "audio", "video"])
    assert all(fig.get_text(strip=True) for fig in soup.find_all("figure"))
    assert len(soup.find_all("figure", class_="card")) == 9
    assert "kg-" not in str(soup)


# ---------------------------------------------------------------- the built book

def routes_for_everything():
    return {
        YT_MAXRES: [ok(jpeg(size=(64, 36)), "image/jpeg")],
        VIMEO_OEMBED: [oembed(title="Bill Murray", thumbnail_url="https://i.vimeocdn.com/video/1.jpg")],
        "https://i.vimeocdn.com/video/1.jpg": [ok(jpeg(), "image/jpeg")],
        SPOTIFY_OEMBED: [oembed(title="Chowder", thumbnail_url="https://image-cdn.spotifycdn.com/i/2")],
        "https://image-cdn.spotifycdn.com/i/2": [ok(jpeg(), "image/jpeg")],
        TIKTOK_OEMBED: [oembed(author_name="Carrot Top", thumbnail_url="https://p16-sign.tiktokcdn-us.com/t.image?x=1")],
        "https://p16-sign.tiktokcdn-us.com/t.image?x=1": [ok(jpeg(size=(36, 64)), "image/jpeg")],
        VIDEO_THUMB: [ok(jpeg(), "image/jpeg")],
        BM_THUMB: [ok(png(), "image/png")],
        "https://img.test/g1.jpg": [ok(jpeg(), "image/jpeg")],
        "https://img.test/g2.jpg": [ok(jpeg(), "image/jpeg")],
    }


EVERYTHING = (YOUTUBE + VIMEO + SPOTIFY + TWEET + BLUESKY + TIKTOK + AUDIO + VIDEO + BOOKMARK + GALLERY + TALLY
              + DAILYMOTION + "<p>The end.</p>")


def test_book_with_every_kind_is_valid(build_book):
    book = build_book(post(GhostPlatform().normalize_html(EVERYTHING)), routes_for_everything())
    assert_valid_epub(book.path)
    chapter = book.chapter()
    assert chapter.count('class="card"') == 10  # 9 known kinds + the generic one; Tally removed
    for leftover in ("data-thumbnail-lookup", "data-fallback-src", "data-oembed-text", "data-decorative",
                     "data-embed-", "<iframe", "<audio", "<video", "<svg", "<input"):
        assert leftover not in chapter
    assert "Carrot Top (@carrottoplive)" in chapter  # display name from TikTok's answer
    assert len(book.image_names()) == 8  # 6 thumbnails + 2 gallery images


def test_thumbnails_are_decorative_not_reported(build_book):
    book = build_book(post(GhostPlatform().normalize_html(EVERYTHING)), routes_for_everything())
    kinds = [p.kind for p in book.result.problems]
    assert kinds.count("image-missing-alt") == 0
    assert sorted(set(kinds)) == ["embed-removed", "embed-unknown"]
    opf = book.text("content.opf")
    assert "alternativeText" in opf
    assert "6 are decorative" in opf


def test_youtube_falls_back_to_the_always_present_thumbnail(build_book):
    routes = {YT_MAXRES: [FakeResponse(404)], YT_MQ: [ok(jpeg(), "image/jpeg")]}
    book = build_book(post(GhostPlatform().normalize_html(YOUTUBE)), routes)
    assert book.result.problems == []
    assert len(book.image_names()) == 1


def test_failed_thumbnail_download_keeps_the_card(build_book):
    routes = {YT_MAXRES: [FakeResponse(404)], YT_MQ: [FakeResponse(404)]}
    book = build_book(post(GhostPlatform().normalize_html(YOUTUBE)), routes)
    [problem] = book.result.problems
    assert problem.kind == "image-download-failed"
    chapter = book.chapter()
    assert 'class="card"' in chapter and "GODZILLA MINUS ZERO" in chapter and "<img" not in chapter
    assert_valid_epub(book.path)


@pytest.mark.parametrize("answer", [FakeResponse(403, b"Approval required", "text/plain"),
                                    FakeResponse(200, b"<html>", "text/html"),
                                    oembed(author_name="Carrot Top")])  # no thumbnail_url
def test_failed_lookup_keeps_the_card_and_reports(build_book, answer):
    book = build_book(post(GhostPlatform().normalize_html(TIKTOK)), {TIKTOK_OEMBED: [answer]})
    [problem] = book.result.problems
    assert problem.kind == "image-download-failed" and problem.url == TIKTOK_OEMBED
    chapter = book.chapter()
    assert "watch on TikTok" in chapter and "<img" not in chapter
    assert "{author_name}" not in chapter
    assert_valid_epub(book.path)


def test_lookup_asked_once_per_book(build_book):
    routes = routes_for_everything()
    session_book = build_book([post(GhostPlatform().normalize_html(TIKTOK), slug="a"),
                               post(GhostPlatform().normalize_html(TIKTOK), slug="b")], routes)
    assert session_book.session.calls[TIKTOK_OEMBED] == 1


def test_removed_and_unknown_embeds_reported(build_book):
    html = GhostPlatform().normalize_html("<p>Vote:</p>" + TALLY + DAILYMOTION)
    book = build_book(post(html), {})
    removed, unknown = book.result.problems
    assert (removed.kind, removed.post_slug) == ("embed-removed", "p")
    assert removed.detail.startswith("Tally form left out") and removed.url.startswith("https://tally.so/embed/")
    assert unknown.kind == "embed-unknown" and unknown.url.startswith("https://www.dailymotion.com/")


def test_cli_summary_lines(capsys):
    build_epub._report_problems([
        BuildProblem("embed-removed", "p", "https://tally.so/embed/x", "Tally form left out"),
        BuildProblem("embed-unknown", "p", "https://dailymotion.com/x", "unrecognised"),
        BuildProblem("embed-unknown", "q", "https://pbs.org/x", "unrecognised"),
    ])
    err = capsys.readouterr().err
    assert "1 embedded form or poll was left out" in err
    assert "2 unrecognised embeds became link cards" in err
    build_epub._report_problems([BuildProblem("embed-unknown", "p", "https://x", "unrecognised")])
    assert "1 unrecognised embed became a link card;" in capsys.readouterr().err


def test_gallery_images_come_through(build_book):
    book = build_book(post(GhostPlatform().normalize_html(GALLERY)),
                      {"https://img.test/g1.jpg": [ok(jpeg(), "image/jpeg")],
                       "https://img.test/g2.jpg": [ok(jpeg(), "image/jpeg")]})
    assert book.result.problems == [] and len(book.image_names()) == 2
    assert_valid_epub(book.path)


def test_card_styles_exist():
    from phoenix_ebook.epub_builder import STYLES_DIR
    css = (STYLES_DIR / "phoenix.css").read_text()
    assert re.search(r"figure\.card\{[^}]*text-align: left", css)
    assert re.search(r"figure\.card img\.thumbnail\{[^}]*max-height", css)
