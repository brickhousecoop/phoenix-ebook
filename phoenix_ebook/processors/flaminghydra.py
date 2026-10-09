"""Flaming Hydra (flaminghydra.com) quirks."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

from phoenix_ebook.canonical import (CALL_TO_ACTION, CALL_TO_ACTION_BANNER, CALL_TO_ACTION_REMOVED,
                                     CALL_TO_ACTION_REMOVED_URL, FEATURE_IMAGE, mark_call_to_action)

from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import HtmlProcessor, register_processor


@register_processor
class FlamingHydraProcessor(HtmlProcessor):
    """Flaming Hydra: drops the comments footer and website calls-to-action (trailing
    subscribe banners; subscribe, share and shop buttons; subscribe links are unwrapped),
    turns "by …" headings into byline paragraphs (dropping one that repeats the chapter
    header's), and labels contents entries "Title — Author"."""

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
        _convert_bylines(soup, post)


# Website calls-to-action (#20). A book can't subscribe, share or shop.
_SUBSCRIBE = re.compile(r"^(?:https?://(?:www\.)?flaminghydra\.(?:com|ghost\.io))?/(?:subscribe|signup|membership|donate)\b",
                        re.I)
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

    _remove_closing_appeal(soup)

    # Subscribe links inside text: keep the words, mark them for the report.
    for link in soup.find_all("a", href=True):
        if _SUBSCRIBE.match(link["href"]) and link.find("img") is None:
            mark_call_to_action(soup, link)


CLOSING_APPEAL_MAX_WORDS = 150  # longer final sections are left alone: more likely writing than an appeal


def _remove_closing_appeal(soup) -> None:
    """Drop a post's final section, after its last <hr>, when it asks readers to subscribe or
    donate (#30): "If you enjoyed this free post, subscribe…". A marker is left for the report."""
    blocks = _blocks(soup)
    rules = [i for i, block in enumerate(blocks) if getattr(block, "name", None) == "hr"]
    if not rules:
        return
    section = blocks[rules[-1] + 1:]
    links = [a["href"] for block in section if hasattr(block, "find_all")
             for a in block.find_all("a", href=True) if _SUBSCRIBE.match(a["href"])]
    text = " ".join(" ".join(block.get_text(" ", strip=True).split()) for block in section).strip()
    if not links or len(text.split()) > CLOSING_APPEAL_MAX_WORDS:
        return
    for block in [blocks[rules[-1]], *section]:
        block.extract()
    _drop_dangling_end(soup)
    soup.append(soup.new_tag("span", attrs={CALL_TO_ACTION_REMOVED: text, CALL_TO_ACTION_REMOVED_URL: links[0]}))


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


# "by …" bylines written as headings (#22): <h4><em>by</em> Name</h4> after a section's heading.
_BYLINE = re.compile(r"^by\s+\S", re.I)
_NAME_SEPARATORS = re.compile(r"\s*,\s*|\s+and\s+|\s*&\s*", re.I)


def _names(text: str) -> set[str]:
    return {" ".join(n.split()).casefold() for n in _NAME_SEPARATORS.split(text) if n.strip()}


def _is_top(block) -> bool:
    """Nothing but the feature image, rules or empty paragraphs before ``block``."""
    previous = _previous_block(block)
    while previous is not None and (_is_filler(previous) or previous.has_attr(FEATURE_IMAGE)):
        previous = _previous_block(previous)
    return previous is None


def _convert_bylines(soup, post: Post) -> None:
    for heading in soup.find_all(["h3", "h4"]):
        text = " ".join(heading.get_text(" ", strip=True).split())
        if not _BYLINE.match(text):
            continue
        previous = _previous_block(heading)
        while previous is not None and _is_filler(previous):
            previous = _previous_block(previous)
        top = _is_top(heading)
        first = heading.find(True)
        italic_by = first is not None and first.name in ("em", "i") and first.get_text(strip=True).casefold() == "by"
        if not (top or italic_by or (previous is not None and previous.name in ("h1", "h2", "h3", "h4", "h5", "h6"))):
            continue  # a heading that happens to start with "By": not a byline
        if top and post.authors and _names(text[2:]) == _names(", ".join(a.name for a in post.authors)):
            heading.decompose()  # repeats the chapter header's byline
            continue
        byline = soup.new_tag("p", attrs={"class": "byline"})
        if heading.get("id"):
            byline["id"] = heading["id"]
        byline.extend(list(heading.contents))
        heading.replace_with(byline)
