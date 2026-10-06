"""Third-party embeds (YouTube, Vimeo, Spotify, X, TikTok, Bluesky, forms) -> canonical link cards.

Recognising an embed is about the provider, not the platform: Ghost (and any
future platform) finds the ``<iframe>`` or quote in its own markup and hands it
here. See "Link cards" in docs/canonical-html.md for the form produced.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from phoenix_ebook.canonical import (CARD_CLASS, DECORATIVE, EMBED_REMOVED, EMBED_SRC, EMBED_UNKNOWN, OEMBED_TEXT,
                                     THUMBNAIL_FALLBACK, THUMBNAIL_LOOKUP)
from phoenix_ebook.dates import format_date

Source = list  # source-line parts joined with " · ": each a string, a tag, or a list of those


def link_card(soup: BeautifulSoup, kind: str, label: str, *, href: str | None = None, title: str | None = None,
              description: str | None = None, quote_: list | None = None, source: Source | None = None,
              thumbnail: str | None = None, thumbnail_fallback: str | None = None,
              thumbnail_lookup: str | None = None) -> Tag:
    """Build a canonical link card. ``quote_`` is a list of block tags (a social post's text)."""
    card = soup.new_tag("figure", attrs={"class": CARD_CLASS, "data-card": kind})
    if thumbnail or thumbnail_lookup:
        img = soup.new_tag("img", attrs={"class": "thumbnail", "alt": "", DECORATIVE: ""})
        if thumbnail:
            img["src"] = thumbnail
        if thumbnail_fallback:
            img[THUMBNAIL_FALLBACK] = thumbnail_fallback
        if thumbnail_lookup:
            img[THUMBNAIL_LOOKUP] = thumbnail_lookup
        card.append(img)
    card.append(_p(soup, "label", label))
    if title:
        p = soup.new_tag("p", attrs={"class": "title"})
        if href:
            a = soup.new_tag("a", href=href)
            a.string = title
            p.append(a)
        else:
            p.string = title
        card.append(p)
    if description:
        card.append(_p(soup, "description", description))
    if quote_:
        blockquote = soup.new_tag("blockquote")
        for block in quote_:
            blockquote.append(block)
        card.append(blockquote)
    if source:
        p = soup.new_tag("p", attrs={"class": "source"})
        for i, part in enumerate(source):
            if i:
                p.append(" · ")
            for node in part if isinstance(part, list) else [part]:
                p.append(node)
        card.append(p)
    return card


def _p(soup, cls: str, text: str) -> Tag:
    p = soup.new_tag("p", attrs={"class": cls})
    p.string = text
    return p


def _a(soup, href: str, text: str) -> Tag:
    a = soup.new_tag("a", href=href)
    a.string = text
    return a


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _host(url: str) -> str:
    return re.sub(r"^www\.", "", urlsplit(url).netloc)


def embed_url(iframe: Tag) -> str:
    """The iframe's URL, including lazy-loading variants (``data-src``, Tally's ``data-tally-src``)."""
    return iframe.get("src") or iframe.get("data-src") or iframe.get("data-tally-src") or ""


# ---------------------------------------------------------------- iframes

def iframe_card(soup: BeautifulSoup, iframe: Tag) -> Tag:
    """What replaces an embed ``<iframe>``: a link card, or a marker for one left out (forms)."""
    src = embed_url(iframe)
    title = (iframe.get("title") or "").strip()
    parts = urlsplit(src)
    host = _host(src)

    if m := re.match(r"/embed/([\w-]{6,})", parts.path) if host in ("youtube.com", "youtube-nocookie.com") else None:
        video = m.group(1)
        return link_card(soup, "video", "Video", href=f"https://www.youtube.com/watch?v={video}",
                         title=title or "YouTube video", source=["YouTube"],
                         # maxres is 16:9 but not always present; mqdefault is always there, also 16:9
                         # (hqdefault is 4:3 with black bars).
                         thumbnail=f"https://i.ytimg.com/vi/{video}/maxresdefault.jpg",
                         thumbnail_fallback=f"https://i.ytimg.com/vi/{video}/mqdefault.jpg")

    if host == "player.vimeo.com" and (m := re.match(r"/video/(\d+)", parts.path)):
        unlisted = parse_qs(parts.query).get("h", [""])[0]
        page = f"https://vimeo.com/{m.group(1)}" + (f"/{unlisted}" if unlisted else "")
        card = link_card(soup, "video", "Video", href=page, title=_vimeo_title(title), source=["Vimeo"],
                         thumbnail_lookup=f"https://vimeo.com/api/oembed.json?url={quote(page, safe='')}&width=1100")
        if title in ("", "vimeo-player"):
            card.find("a")[OEMBED_TEXT] = "{title}"
        return card

    if host == "open.spotify.com" and (m := re.match(r"/embed/(\w+)/(\w+)", parts.path)):
        page = f"https://open.spotify.com/{m.group(1)}/{m.group(2)}"
        name = re.sub(r"^Spotify Embed:\s*", "", title)
        card = link_card(soup, "audio", "Audio", href=page, title=name or f"Spotify {m.group(1)}",
                         source=["Spotify"],
                         thumbnail_lookup=f"https://open.spotify.com/oembed?url={quote(page, safe='')}")
        if not name:
            card.find("a")[OEMBED_TEXT] = "{title}"
        return card

    if host == "tally.so":
        what = "Tally form" + (f" \"{title}\"" if title and title != "Tally Forms" else "")
        return soup.new_tag("div", attrs={EMBED_REMOVED: what, EMBED_SRC: src})

    href = src if parts.scheme in ("http", "https") else None
    card = link_card(soup, "embed", "Embedded content", href=href, title=title or host or "Embedded content",
                     source=[host] if host else None)
    card[EMBED_UNKNOWN] = src
    return card


def _vimeo_title(title: str) -> str:
    return "Vimeo video" if title in ("", "vimeo-player") else title


# ---------------------------------------------------------------- social posts

def social_card(soup: BeautifulSoup, blockquote: Tag) -> Tag | None:
    """A social post's quote (X/Twitter, TikTok, Bluesky embed code) as a link card, or None if not one."""
    classes = blockquote.get("class", [])
    if "twitter-tweet" in classes:
        return _tweet(soup, blockquote)
    if "tiktok-embed" in classes:
        return _tiktok(soup, blockquote)
    if "bluesky-embed" in classes:
        return _bluesky(soup, blockquote)
    return None


def _direct_links(blockquote: Tag) -> list[Tag]:
    return [a for a in blockquote.find_all("a", recursive=False) if a.get("href")]


def _byline_text(blockquote: Tag) -> str:
    """'— Name (@handle)' text between the post and its date link -> 'Name (@handle)'."""
    text = "".join(c for c in blockquote.children if isinstance(c, NavigableString))
    return " ".join(text.split()).strip("—– ").strip()


def _tweet(soup, blockquote: Tag) -> Tag:
    body = blockquote.find("p")
    links = _direct_links(blockquote)
    date_link = links[-1] if links else None
    href = _strip_query(date_link["href"]) if date_link else None
    media = []
    if body is not None:
        for a in body.find_all("a", href=True):
            if re.match(r"(pic\.twitter\.com|pic\.x\.com)/", a.get_text(strip=True)):
                media.append(a["href"])
                a.decompose()
    byline = _byline_text(blockquote)
    source: Source = [byline] if byline else []
    if date_link is not None:
        source.append(_a(soup, href, _tweet_date(date_link.get_text(strip=True))))
    if media:
        source.append(["attached media: ", _a(soup, media[0], "view on X")])
    return link_card(soup, "post", "Post on X", quote_=_paragraphs(soup, body), source=source)


def _tweet_date(text: str) -> str:
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return format_date(datetime.strptime(text, fmt).date().isoformat()) or text
        except ValueError:
            pass
    return text


def _bluesky(soup, blockquote: Tag) -> Tag:
    body = blockquote.find("p")
    links = _direct_links(blockquote)
    source: Source = []
    if len(links) >= 2:
        source.append(links[-2].get_text(" ", strip=True))
    if links:
        stamp = links[-1].get_text(strip=True)
        source.append(_a(soup, _strip_query(links[-1]["href"]), format_date(stamp) or stamp))
    return link_card(soup, "post", "Post on Bluesky", quote_=_paragraphs(soup, body), source=source)


def _tiktok(soup, blockquote: Tag) -> Tag:
    video = _strip_query(blockquote.get("cite") or "")
    author = blockquote.find("a", title=re.compile(r"^@"))
    handle = author.get_text(strip=True) if author else ""
    body = blockquote.find("p")
    if body is not None:
        for a in body.find_all("a"):
            a.unwrap()  # hashtag pages: noise in a book
    source: Source = []
    if handle:
        span = soup.new_tag("span", attrs={OEMBED_TEXT: "{author_name} (" + handle.replace("{", "{{").replace("}", "}}") + ")"})
        span.string = handle
        source.append(span)
    if video:
        source.append(_a(soup, video, "watch on TikTok"))
    return link_card(soup, "video", "Video on TikTok", quote_=_paragraphs(soup, body), source=source,
                     thumbnail_lookup=f"https://www.tiktok.com/oembed?url={quote(video, safe='')}" if video else None)


def _paragraphs(soup, body: Tag | None) -> list[Tag]:
    """A post's text as paragraphs: blank lines split paragraphs, single newlines become <br>."""
    if body is None:
        return []
    html = body.decode_contents().strip()
    paragraphs = []
    for chunk in re.split(r"\n\s*\n", html):
        if not chunk.strip():
            continue
        p = soup.new_tag("p")
        lines = chunk.strip().split("\n")
        for i, line in enumerate(lines):
            if i:
                p.append(soup.new_tag("br"))
            for node in list(BeautifulSoup(line, "html.parser").contents):
                p.append(node)
        paragraphs.append(p)
    return paragraphs
