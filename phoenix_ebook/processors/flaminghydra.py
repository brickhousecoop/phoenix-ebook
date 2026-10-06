"""Flaming Hydra (flaminghydra.com) quirks."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

from phoenix_ebook.canonical import CALL_TO_ACTION, CALL_TO_ACTION_BANNER, mark_call_to_action

from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import HtmlProcessor, register_processor


@register_processor
class FlamingHydraProcessor(HtmlProcessor):
    """Flaming Hydra: drops the comments footer and website calls-to-action (trailing
    subscribe banners; subscribe, share and shop buttons; subscribe links are unwrapped),
    and labels contents entries "Title — Author"."""

    name = "flaminghydra"
    platform = "ghost"
    sites = ("flaminghydra",)  # flaminghydra.ghost.io and flaminghydra.com

    def display_title(self, post: Post) -> str:
        """Collections mix many writers, so the contents show who wrote each piece."""
        names = [a.name for a in post.authors]
        if not names:
            return post.title
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"{post.title} — {joined}"

    def clean(self, soup: BeautifulSoup, post: Post) -> None:
        super().clean(soup, post)
        # Posts end with an <hr> and a line inviting readers to the site's comments
        # ("You may Shred in the Comments Section", linking to #comments). It means
        # nothing in a book, so drop the first such <hr> + paragraph pair.
        for hr in soup.find_all("hr"):
            next_sib = hr.find_next_sibling()
            if next_sib and next_sib.name == "p":
                link = next_sib.find("a", href=lambda h: h and "#comments" in h)
                if link:
                    next_sib.decompose()
                    hr.decompose()
                    break
        _remove_calls_to_action(soup)


# Website calls-to-action (#20). A book can't subscribe, share or shop.
_SUBSCRIBE = re.compile(r"^(?:https?://(?:www\.)?flaminghydra\.(?:com|ghost\.io))?/(?:subscribe|signup|membership)\b", re.I)
_SHARE = re.compile(r"^https?://(?:bsky\.app/intent/|(?:www\.)?(?:twitter|x)\.com/intent/|"
                    r"(?:www\.)?facebook\.com/sharer|(?:www\.)?threads\.net/intent/)", re.I)
_SHOP = re.compile(r"^https?://shop\.flaminghydra\.com\b", re.I)
_CTA_HEADING = re.compile(r"free post|subscri|support our serpent|support us", re.I)


def _remove_calls_to_action(soup) -> None:
    # Buttons (canonical <p class="button">) that subscribe, share or shop: removed whole.
    for button in soup.find_all("p", class_="button"):
        link = button.find("a", href=True)
        if link and any(p.match(link["href"]) for p in (_SUBSCRIBE, _SHARE, _SHOP)):
            button.decompose()

    # Subscribe banners: removed only at the end of the post; elsewhere they may be content.
    banners = [fig for fig in soup.find_all("figure") if _is_subscribe_banner(fig)]
    trailing = _trailing_banners(soup, banners)
    for figure in banners:
        if figure in trailing:
            heading = _previous_block(figure)
            if heading is not None and heading.name in ("h2", "h3", "h4") and _CTA_HEADING.search(heading.get_text()):
                heading.decompose()
            figure.decompose()
        else:
            figure[CALL_TO_ACTION_BANNER] = ""
    if trailing:
        _drop_dangling_end(soup)

    # Subscribe links inside text: keep the words, mark them for the report.
    for link in soup.find_all("a", href=True):
        if _SUBSCRIBE.match(link["href"]) and link.find("img") is None:
            mark_call_to_action(soup, link)


def _is_subscribe_banner(figure) -> bool:
    link = figure.find("a", href=True)
    return bool(link and link.find("img") is not None and _SUBSCRIBE.match(link["href"]))


def _blocks(soup) -> list:
    """Top-level content blocks of the post, ignoring whitespace between them."""
    return [c for c in soup.contents if not (isinstance(c, NavigableString) and not c.strip())]


def _is_filler(block) -> bool:
    """A block that carries no content of its own: a rule, or an empty paragraph."""
    if getattr(block, "name", None) == "hr":
        return True
    return getattr(block, "name", None) == "p" and not block.get_text(strip=True) and block.find("img") is None


def _trailing_banners(soup, banners) -> set:
    """Banners followed only by fillers, call-to-action headings or other trailing banners."""
    trailing = set()
    for block in reversed(_blocks(soup)):
        if block in banners:
            trailing.add(block)
        elif _is_filler(block) or (block.name in ("h2", "h3", "h4") and _CTA_HEADING.search(block.get_text())):
            continue
        else:
            break
    return trailing


def _previous_block(tag):
    sibling = tag.previous_sibling
    while sibling is not None and isinstance(sibling, NavigableString) and not sibling.strip():
        sibling = sibling.previous_sibling
    return sibling


def _drop_dangling_end(soup) -> None:
    """After removing a trailing banner, drop rules and empty paragraphs left at the end."""
    for block in reversed(_blocks(soup)):
        if _is_filler(block):
            block.decompose()
        else:
            break
