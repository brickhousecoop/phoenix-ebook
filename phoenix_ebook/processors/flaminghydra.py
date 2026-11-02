"""Flaming Hydra (flaminghydra.com) quirks."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from phoenix_ebook.canonical import CALL_TO_ACTION_BANNER, FEATURE_IMAGE

from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import HtmlProcessor, register_processor
from phoenix_ebook.processors.rules import (CLOSING_APPEAL_MAX_WORDS, SiteRules, blocks,  # noqa: F401 — tests read the limit
                                            drop_dangling_end, is_filler, previous_block, remove_buttons,
                                            remove_final_sections, remove_sales_lines, unlink_sales_links)


@register_processor
class FlamingHydraProcessor(HtmlProcessor):
    """Flaming Hydra: drops the comments footer and trailing subscribe banners, applies its
    rules (``sites/flaminghydra.toml``), turns "by …" headings into byline paragraphs (dropping one that repeats the chapter
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

    def apply_rules(self, soup: BeautifulSoup, post: Post) -> None:
        _remove_comments_invitations(soup)
        remove_buttons(soup, self.rules, report=False)  # buttons were never reported here (#20)
        _remove_trailing_banners(soup, self.rules)
        remove_final_sections(soup, self.rules, heading=_CAMPAIGN_HEADING)
        unlink_sales_links(soup, self.rules)
        remove_sales_lines(soup, self.rules)
        _convert_bylines(soup, post)


COMMENTS_INVITATION_MAX_WORDS = 30  # after a rule, a longer paragraph mentioning the comments is writing


def _is_comments_link(href) -> bool:
    return bool(href) and "#comments" in href


def _remove_comments_invitations(soup) -> None:
    """Drop the lines inviting readers to the site's comments ("You may Shred in the Comments
    Section", linking to #comments). They mean nothing in a book. Most posts end with one after an
    <hr>; some have no rule, or put it above the share buttons. A paragraph is dropped when its
    words are all comments links, or when it follows an <hr> (dropped too), is short and has one."""
    removed = False
    for p in soup.find_all("p"):
        if p.find("a", href=_is_comments_link) is None:
            continue
        rest = "".join(s for s in p.strings if s.find_parent("a", href=_is_comments_link) is None)
        previous = previous_block(p)
        after_rule = (getattr(previous, "name", None) == "hr"
                      and len(p.get_text(" ").split()) <= COMMENTS_INVITATION_MAX_WORDS)
        if after_rule or not re.search(r"\w", rest):
            if after_rule:
                previous.decompose()
            p.decompose()
            removed = True
    if removed:
        drop_dangling_end(soup)


# Words that name a campaign or a call-to-action in a heading; addresses are in sites/flaminghydra.toml.
_CAMPAIGN_HEADING = re.compile(r"\bkickstarter\b|\bthe phoenix\b", re.I)
_CTA_HEADING = re.compile(r"free post|subscri|support our serpent|support us", re.I)


def _remove_trailing_banners(soup, rules: SiteRules) -> None:
    """Subscribe banners: removed only at the end of the post; elsewhere they may be content (#20)."""
    banners = [fig for fig in soup.find_all("figure") if _is_subscribe_banner(fig, rules)]
    trailing = _trailing_banners(soup, banners)
    for figure in banners:
        if figure in trailing:
            heading = previous_block(figure)
            if heading is not None and heading.name in ("h2", "h3", "h4") and _CTA_HEADING.search(heading.get_text()):
                heading.decompose()
            figure.decompose()
        else:
            figure[CALL_TO_ACTION_BANNER] = ""
    if trailing:
        drop_dangling_end(soup)


def _is_subscribe_banner(figure, rules: SiteRules) -> bool:
    link = figure.find("a", href=True)
    return bool(link and link.find("img") is not None and rules.is_sales_link(link["href"]))


def _trailing_banners(soup, banners) -> set:
    """Banners followed only by fillers, call-to-action headings or other trailing banners."""
    trailing = set()
    for block in reversed(blocks(soup)):
        if block in banners:
            trailing.add(block)
        elif is_filler(block) or (block.name in ("h2", "h3", "h4") and _CTA_HEADING.search(block.get_text())):
            continue
        else:
            break
    return trailing


# "by …" bylines written as headings (#22): <h4><em>by</em> Name</h4> after a section's heading.
_BYLINE = re.compile(r"^by\s+\S", re.I)
_NAME_SEPARATORS = re.compile(r"\s*,\s*|\s+and\s+|\s*&\s*", re.I)


def _names(text: str) -> set[str]:
    return {" ".join(n.split()).casefold() for n in _NAME_SEPARATORS.split(text) if n.strip()}


def _is_top(block) -> bool:
    """Nothing but the feature image, rules or empty paragraphs before ``block``."""
    previous = previous_block(block)
    while previous is not None and (is_filler(previous) or previous.has_attr(FEATURE_IMAGE)):
        previous = previous_block(previous)
    return previous is None


def _convert_bylines(soup, post: Post) -> None:
    for heading in soup.find_all(["h3", "h4"]):
        text = " ".join(heading.get_text(" ", strip=True).split())
        if not _BYLINE.match(text):
            continue
        previous = previous_block(heading)
        while previous is not None and is_filler(previous):
            previous = previous_block(previous)
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
