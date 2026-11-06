"""WordPress full-text feed platform: posts from a site's private RSS feed (#49, ADR 0005).

The secret is the whole private feed address (e.g. ``https://defector.com/feed/<id>?key=…``): a
feed id and a key can both be in it, so it's never logged, printed or put in an error message. A
post is fetched by adding WordPress's own ``name=<slug>`` to that address, which answers with that
one post (full text in ``content:encoded``), or 404.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from phoenix_ebook.canonical import EMBED_UNKNOWN, MARKUP_UNKNOWN
from phoenix_ebook.embeds import iframe_card, link_card, social_card
from phoenix_ebook.errors import AuthError, PostNotFound, SourceUnreachable
from phoenix_ebook.models import Author, Post
from phoenix_ebook.platforms.base import Platform, register_platform

_NS = {"content": "http://purl.org/rss/1.0/modules/content/", "dc": "http://purl.org/dc/elements/1.1/",
       "media": "http://search.yahoo.com/mrss/"}
LEAD_IMAGE_WIDTH = 1600  # the feed links a 564-pixel copy; the build shrinks this one to its own size

# Blocks whose wrapper is only layout: the contents stay, the wrapper goes.
_LAYOUT_BLOCKS = {"group", "columns", "column", "media-text", "media-text__content", "media-text__media", "cover",
                  "cover__inner-container", "embed__wrapper", "buttons", "freeform", "html"}
# Blocks that are already ordinary HTML once their classes are gone.
_PLAIN_BLOCKS = {"paragraph", "heading", "list", "list-item", "quote", "separator", "image", "table", "preformatted",
                 "code", "verse", "details", "footnotes", "gallery", "pullquote", "video", "audio", "button",
                 "file", "spacer", "embed"}


@register_platform
class WordPressFeedPlatform(Platform):
    """Fetches posts one at a time from a WordPress site's private full-text RSS feed.

    ``url`` is the site's public address (``https://defector.com``); ``secret`` is the full private
    feed address, which must be on the same site. Dates come in UTC (``dates_in_utc``): a blog's
    ``timezone`` setting in ``phoenix_ebook/sites/`` puts them in the site's own time zone.
    """

    name = "wordpress-feed"
    default_processor = "generic"
    dates_in_utc = True

    def normalize_html(self, html: str) -> str:
        """WordPress block HTML -> canonical chapter HTML (docs/canonical-html.md).

        Embeds, videos and audio become link cards; image, quote, table and gallery blocks
        become plain HTML; emoji pictures become the emoji again; layout wrappers are unwrapped; ``wp-*`` classes and responsive
        image attributes go. A block it doesn't know keeps its contents and is marked
        (``data-markup-unknown``) for the build report.
        """
        if "wp-" not in html and "<iframe" not in html and "<blockquote" not in html:
            return html
        soup = BeautifulSoup(html, "html.parser")
        _convert_media(soup)
        _convert_blocks(soup)
        return str(soup)

    def fetch_post(self, session: requests.Session, url: str, secret: str, slug: str) -> Post:
        site = (urlsplit(url).hostname or url).lower()
        feed = urlsplit(secret.strip())
        feed_host = (feed.hostname or "").lower()
        if feed.scheme not in ("http", "https") or not feed_host:
            raise AuthError(f"the stored feed for {site} isn't a web address. Store the private feed address "
                            f"again: build_epub.py --set-secret --platform {self.name} --url {url}")
        if _bare(feed_host) != _bare(site):
            raise AuthError(f"the stored feed for {site} is on a different site ({feed_host}). Store {site}'s "
                            f"private feed address: build_epub.py --set-secret --platform {self.name} --url {url}")
        query = "&".join(part for part in (feed.query, f"name={quote(slug, safe='')}") if part)
        try:
            r = session.get(urlunsplit((feed.scheme, feed.netloc, feed.path, query, "")), timeout=30)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise SourceUnreachable(f"can't reach {site}: {type(exc).__name__}. Check your network connection.") \
                from None  # the request's own message would show the feed address
        if r.status_code == 404:
            raise PostNotFound(site, [slug])
        if r.status_code in (401, 403):
            raise AuthError(f"{site} refused the private feed (HTTP {r.status_code}). Its key may have changed: "
                            f"store the current feed address with build_epub.py --set-secret --platform {self.name} "
                            f"--url {url}")
        if not 200 <= r.status_code < 300:
            raise SourceUnreachable(f"can't reach {site}'s feed (HTTP {r.status_code}). Try again in a few minutes.")
        item = _only_item(r.content, site)
        if item is None:
            raise PostNotFound(site, [slug])
        return self._post(item, slug)

    def _post(self, item: ET.Element, slug: str) -> Post:
        link = item.findtext("link") or ""
        media = item.find("media:content", _NS)
        lead = media.get("url") if media is not None else None
        html = item.findtext("content:encoded", namespaces=_NS) or item.findtext("description") or ""
        return Post(
            slug=urlsplit(link).path.strip("/").rsplit("/", 1)[-1] or slug,
            title=" ".join((item.findtext("title") or slug).split()),
            html=self.normalize_html(html),
            authors=[Author(name=" ".join(c.text.split())) for c in item.findall("dc:creator", _NS)
                     if c.text and c.text.strip()],
            published_at=_utc_timestamp(item.findtext("pubDate")),
            feature_image=_sized(lead, LEAD_IMAGE_WIDTH) if lead else None,
            categories=[" ".join(c.text.split()) for c in item.findall("category") if c.text and c.text.strip()],
            raw={"link": link, "guid": item.findtext("guid")},
        )


def _bare(host: str) -> str:
    return host.removeprefix("www.")


def _only_item(body: bytes, site: str) -> ET.Element | None:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise SourceUnreachable(f"{site}'s feed didn't answer with a feed. Check that the stored address is the "
                                "private feed's.") from None
    if root.find("channel") is None:
        raise SourceUnreachable(f"{site}'s feed didn't answer with an RSS feed. Check that the stored address is "
                                "the private feed's.")
    items = root.findall("channel/item")
    return items[0] if len(items) == 1 else None  # more than one: the feed ignored name=, so no single post


def _utc_timestamp(pub_date: str | None) -> str | None:
    if not pub_date:
        return None
    try:
        return parsedate_to_datetime(pub_date).isoformat()
    except (TypeError, ValueError):
        return None


def _sized(url: str, width: int) -> str:
    """The image at ``width`` pixels (WordPress/Jetpack's ``?w=``), replacing any size in the query."""
    parts = urlsplit(url)
    query = [q for q in parts.query.split("&") if q and not re.match(r"(w|h|resize|fit)=", q)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join([*query, f"w={width}"]), ""))


def _block_names(tag: Tag) -> list[str]:
    """'wp-block-quote-is-layout-flow' -> 'quote', 'wp-block-gallery-1' -> 'gallery', 'wp-block-media-text__media'
    stays whole (its own element of a block)."""
    names = (c[len("wp-block-"):] for c in tag.get("class", []) if c.startswith("wp-block-"))
    return list(dict.fromkeys(re.sub(r"-is-layout-[\w-]+$|-\d+$", "", n) for n in names))


def _caption(figure: Tag) -> Tag | None:
    caption = figure.find("figcaption", recursive=False)
    return caption.extract() if caption is not None else None


def _with_caption(card: Tag, caption: Tag | None) -> Tag:
    if caption is not None and card.name == "figure":
        card.append(caption)
    return card


def _convert_media(soup: BeautifulSoup) -> None:
    """Embed, video and audio blocks (and loose embeds) -> link cards (``phoenix_ebook.embeds``)."""
    for figure in soup.find_all("figure", class_="wp-block-embed"):
        caption = _caption(figure)
        iframe, quote_ = figure.find("iframe"), figure.find("blockquote")
        card = (iframe_card(soup, iframe) if iframe is not None
                else social_card(soup, quote_) if quote_ is not None else None)
        if card is None:
            link = figure.find("a", href=True)
            page = link["href"] if link else figure.get_text(" ", strip=True)
            if not re.match(r"https?://", page or ""):
                for script in figure.find_all("script"):
                    script.decompose()
                continue  # nothing to link to: the block's contents stay, unwrapped below
            card = link_card(soup, "embed", "Embedded content", href=page, title=page,
                             source=[urlsplit(page).netloc.removeprefix("www.")])
            card[EMBED_UNKNOWN] = page
        figure.replace_with(_with_caption(card, caption))

    for kind, label, title in (("video", "Video", "Watch the video"), ("audio", "Audio", "Listen")):
        for figure in soup.find_all("figure", class_=f"wp-block-{kind}"):
            media = figure.find(kind)
            src = media.get("src") if media is not None else None
            if not src and media is not None and (source := media.find("source")) is not None:
                src = source.get("src")
            ext = urlsplit(src or "").path.rsplit(".", 1)[-1].upper() if src and "." in src.rsplit("/", 1)[-1] else ""
            card = link_card(soup, kind, label, href=src or None, title=title,
                             source=[f"{ext} {kind}" if ext else f"{kind.capitalize()} file"],
                             thumbnail=(media.get("poster") or None) if media else None)
            figure.replace_with(_with_caption(card, _caption(figure)))

    for iframe in soup.find_all("iframe"):  # pasted into a Custom HTML block
        iframe.replace_with(iframe_card(soup, iframe))


def _convert_blocks(soup: BeautifulSoup) -> None:
    """Every other block: plain HTML, wrappers unwrapped, unknown blocks marked."""
    for script in soup.find_all(["script", "noscript"]):
        script.decompose()
    for emoji in soup.find_all("img", src=re.compile(r"^(https?:)?//s\.w\.org/images/core/emoji/")):
        emoji.replace_with(emoji.get("alt") or "")  # WordPress swaps emoji for pictures; the alt is the emoji
    for spacer in soup.find_all(class_="wp-block-spacer"):
        spacer.decompose()

    for figure in soup.find_all("figure", class_="wp-block-pullquote"):
        quote_ = figure.find("blockquote")
        if quote_ is not None:
            quote_["class"] = ["pullquote"]
            figure.replace_with(quote_.extract())

    for gallery in soup.find_all("figure", class_="wp-block-gallery"):
        caption = _caption(gallery)
        for inner in gallery.find_all("figure", recursive=False):
            gallery.insert_before(inner.extract())
        if caption is not None and caption.get_text(strip=True):
            p = soup.new_tag("p")
            p.extend(list(caption.contents))
            gallery.insert_before(p)
        gallery.decompose()

    for button in soup.find_all(class_="wp-block-button"):
        link = button.find("a", href=True)
        if link is None:
            button.unwrap()
            continue
        p = soup.new_tag("p", attrs={"class": "button"})
        a = soup.new_tag("a", href=link["href"])
        a.extend(list(link.contents))
        p.append(a)
        button.replace_with(p)

    for table in soup.find_all("figure", class_="wp-block-table"):
        if table.find("figcaption") is None:
            table.unwrap()

    for tag in soup.find_all(class_=True):
        if tag.parent is None:  # inside a block already removed
            continue
        names = _block_names(tag)
        unknown = [n for n in names if n not in _PLAIN_BLOCKS | _LAYOUT_BLOCKS and not n.startswith("embed")]
        if unknown:
            tag.insert_before(soup.new_tag("span", attrs={MARKUP_UNKNOWN: f"wp-block-{unknown[0]}"}))
        if tag.name in ("div", "section", "span") and (unknown or any(n in _LAYOUT_BLOCKS for n in names)):
            tag.unwrap()
            continue
        classes = [c for c in tag.get("class", []) if not _is_wordpress_class(c)]
        if classes:
            tag["class"] = classes
        else:
            del tag["class"]

    for tag in soup.find_all(["img", "video", "audio", "source", "blockquote", "figure"]):
        for attr in ("srcset", "sizes", "fetchpriority", "data-id", "data-width", "data-dnt", "data-align"):
            tag.attrs.pop(attr, None)


def _is_wordpress_class(cls: str) -> bool:
    return bool(re.match(r"(wp-|is-|has-|align|size-|are-vertically|is_|alignment-)", cls))
