"""Flaming Hydra (flaminghydra.com) quirks."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from phoenix_ebook.canonical import (CALL_TO_ACTION, CALL_TO_ACTION_BANNER, CALL_TO_ACTION_REMOVED,
                                     CALL_TO_ACTION_REMOVED_URL, FEATURE_IMAGE, mark_call_to_action)

from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import (HtmlProcessor, blocks, drop_dangling_end, is_filler, previous_block,
                                            register_processor)


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
        _remove_comments_invitations(soup)
        _remove_calls_to_action(soup)
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


# Website calls-to-action (#20). A book can't subscribe, share or shop.
_SUBSCRIBE = re.compile(r"^(?:https?://(?:www\.)?flaminghydra\.(?:com|ghost\.io))?/(?:subscribe|signup|membership|donate)\b",
                        re.I)
_SHARE = re.compile(r"^https?://(?:bsky\.app/intent/|(?:www\.)?(?:twitter|x)\.com/intent/|"
                    r"(?:www\.)?facebook\.com/sharer|(?:www\.)?threads\.net/intent/)", re.I)
_SHOP = re.compile(r"^https?://shop\.flaminghydra\.com\b", re.I)
# Fundraising campaigns (#39): the blog's Kickstarter projects, and The Phoenix's sign-up form.
_CAMPAIGN_LINK = re.compile(r"^https?://(?:www\.)?(?:kickstarter\.com/projects/thebrickhouse/|tally\.so/r/7RyjGA\b)", re.I)
_CAMPAIGN_HEADING = re.compile(r"\bkickstarter\b|\bthe phoenix\b", re.I)
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
            heading = previous_block(figure)
            if heading is not None and heading.name in ("h2", "h3", "h4") and _CTA_HEADING.search(heading.get_text()):
                heading.decompose()
            figure.decompose()
        else:
            figure[CALL_TO_ACTION_BANNER] = ""
    if trailing:
        drop_dangling_end(soup)

    _remove_closing_appeal(soup)

    # Subscribe links inside text: keep the words, mark them for the report.
    for link in soup.find_all("a", href=True):
        if _SUBSCRIBE.match(link["href"]) and link.find("img") is None:
            mark_call_to_action(soup, link)


CLOSING_APPEAL_MAX_WORDS = 150  # longer final sections are left alone: more likely writing than an appeal


def _remove_closing_appeal(soup) -> None:
    """Drop a post's final section, after its last <hr>, when it asks readers to subscribe or donate
    (#30: "If you enjoyed this free post, subscribe…") or promotes a campaign (#39: "DID WE TELL YOU
    ABOUT OUR KICKSTARTER?"), and any such section that leaves last. Markers are left for the report."""
    markers = []
    while marker := _remove_final_appeal(soup):
        markers.append(marker)
    soup.extend(markers)


def _remove_final_appeal(soup):
    parts = blocks(soup)
    rules = [i for i, block in enumerate(parts) if getattr(block, "name", None) == "hr"]
    if not rules:
        return None
    section = [block for block in parts[rules[-1] + 1:] if hasattr(block, "find_all")]
    hrefs = [a["href"] for block in section for a in block.find_all("a", href=True)]
    links = [href for href in hrefs if _SUBSCRIBE.match(href)] + [href for href in hrefs if _CAMPAIGN_LINK.match(href)]
    text = " ".join(" ".join(block.get_text(" ", strip=True).split()) for block in section).strip()
    if not (any(_SUBSCRIBE.match(href) for href in hrefs) or _is_campaign_promo(section)) \
            or len(text.split()) > CLOSING_APPEAL_MAX_WORDS:
        return None
    for block in [parts[rules[-1]], *section]:
        block.extract()
    drop_dangling_end(soup)
    attrs = {CALL_TO_ACTION_REMOVED: text}
    if links:
        attrs[CALL_TO_ACTION_REMOVED_URL] = links[0]
    return soup.new_tag("span", attrs=attrs)


def _is_campaign_promo(section: list) -> bool:
    """A section about a campaign, with no writing mixed in: every paragraph links to the campaign."""
    def has_link(block) -> bool:
        return any(_CAMPAIGN_LINK.match(a["href"]) for a in block.find_all("a", href=True))

    named = any(block.name in ("h2", "h3", "h4") and _CAMPAIGN_HEADING.search(block.get_text()) for block in section)
    paragraphs = [block for block in section if block.name == "p" and block.get_text(strip=True)]
    return (named or any(has_link(block) for block in section)) and all(has_link(p) for p in paragraphs)


def _is_subscribe_banner(figure) -> bool:
    link = figure.find("a", href=True)
    return bool(link and link.find("img") is not None and _SUBSCRIBE.match(link["href"]))


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
