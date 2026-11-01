"""Welcome to Hell World (welcometohellworld.com) quirks."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from phoenix_ebook.canonical import (CALL_TO_ACTION, CALL_TO_ACTION_REMOVED, CALL_TO_ACTION_REMOVED_AS,
                                     CALL_TO_ACTION_REMOVED_URL, FEATURE_IMAGE)
from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import HtmlProcessor, drop_dangling_end, previous_block, register_processor

# A line that is nothing but one of these is selling, linked or not. Longer sentences may be writing.
_SALES_LINE = re.compile(r"""
    subscribe(\ now)?(\ please\ and\ thank\ you)?
    | subscribe\ -\ .{1,40}                    # "Subscribe - first month FREE"
    | (get\ )?\d+%\ off\b.{0,40}               # "Get 20% off for 1 year", "33% off one year of Hell World"
    | \d+\ day\ free\ trial
    | give\ a\ gift\ subscription
    | support\ (independent|indie)\ media
    | share(\ welcome\ to\ hell\ world)?
    | welcome\ to\ hell\ world\ is\ a\ reader-supported\ publication\b.{0,120}
""", re.I | re.X)

# The paywall's marker, however it's worded ("To read the rest please subscribe"). In a book the rest usually
# just follows; a line linking elsewhere with little after it means this post really is shortened, so it stays.
_PAYWALL_LINE = re.compile(r"\b(?:read|unlock)\b.{0,10}\bthe rest\b.*\bsubscri|\bsubscri.*\b(?:read|unlock)\b.{0,10}\bthe rest\b", re.I)
PAYWALL_LINE_MAX_WORDS = 35
REST_OF_POST_MIN_WORDS = 100


@register_processor
class HellWorldProcessor(HtmlProcessor):
    """Welcome to Hell World: drops the buttons (every one sells a subscription or a book) and
    the lines that only sell a subscription, including "Subscribe to read the rest"; each is
    reported. Mentions in longer sentences, "Read the rest here" links and book notices stay."""

    name = "hellworld"
    platform = "ghost"
    sites = ("hellworld",)  # hellworld.ghost.io and welcometohellworld.com

    def clean(self, soup: BeautifulSoup, post: Post) -> None:
        super().clean(soup, post)
        _remove_sales(soup)


def _remove_sales(soup) -> None:
    removed = []
    for block in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
        if block.find_parent(["blockquote", "figure"]) is not None or block.find("img") is not None:
            continue  # someone else's words, or an image that may be content
        words = " ".join(block.get_text(" ", strip=True).split())
        is_button = "button" in block.get("class", [])
        if not words or not (is_button or _only_sells(block, words) or _all_marked(block)):
            continue
        removed.append(_marker(soup, block, words, "button" if is_button else "sales line"))
        _remove_block(block)
    if removed:
        drop_dangling_end(soup)
        soup.extend(removed)


def _only_sells(block, words: str) -> bool:
    if _SALES_LINE.fullmatch(words.rstrip(" .!")):
        return True
    if len(words.split()) > PAYWALL_LINE_MAX_WORDS or not _PAYWALL_LINE.search(words):
        return False
    return block.find("a", href=True) is None or _words_after(block) >= REST_OF_POST_MIN_WORDS


def _words_after(block) -> int:
    return sum(len(sibling.get_text(" ").split()) for sibling in block.find_next_siblings())


def _all_marked(block) -> bool:
    """Whether every word of ``block`` was a call-to-action link (the platform unwraps and marks them)."""
    marks = block.find_all("span", attrs={CALL_TO_ACTION: True})
    rest = "".join(s for s in block.strings if s.find_parent("span", attrs={CALL_TO_ACTION: True}) is None)
    return bool(marks) and not rest.strip()


def _marker(soup, block, words: str, kind: str):
    attrs = {CALL_TO_ACTION_REMOVED: words, CALL_TO_ACTION_REMOVED_AS: kind}
    link = block.find("a", href=True)
    mark = block.find("span", attrs={CALL_TO_ACTION: True})
    if url := (link["href"] if link else mark[CALL_TO_ACTION] if mark else None):
        attrs[CALL_TO_ACTION_REMOVED_URL] = url
    return soup.new_tag("span", attrs=attrs)


def _remove_block(block) -> None:
    """Remove ``block``; a removal mustn't leave two rules in a row, or a rule opening the post."""
    before, after = previous_block(block), block.find_next_sibling()
    block.extract()
    at_top = before is None or (hasattr(before, "has_attr") and before.has_attr(FEATURE_IMAGE))
    if after is not None and after.name == "hr" and (at_top or getattr(before, "name", None) == "hr"):
        after.decompose()
