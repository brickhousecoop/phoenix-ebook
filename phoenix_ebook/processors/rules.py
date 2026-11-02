"""Cleanup rules every blog shares, and the plain files that add to them (``phoenix_ebook/sites/``).

What a blog removes is data: phrases and addresses, read from ``sites/<processor name>.toml``, on top
of ``sites/_defaults.toml``. How it's removed is here. A phrase matches a whole line, ignoring capitals
and final punctuation; ``#`` is any number, ``*`` any words. See "Blog rule files" in the README.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import NavigableString

from phoenix_ebook.canonical import (CALL_TO_ACTION, CALL_TO_ACTION_REMOVED, CALL_TO_ACTION_REMOVED_AS,
                                     CALL_TO_ACTION_REMOVED_URL, FEATURE_IMAGE, mark_call_to_action)
from phoenix_ebook.errors import SiteFileError

SITES_DIR = Path(__file__).parent.parent / "sites"
DEFAULTS_FILE = "_defaults.toml"

LINE_MAX_WORDS = 35  # a longer block is more likely writing than a line that only sells
CLOSING_APPEAL_MAX_WORDS = 150  # longer final sections are left alone: more likely writing than an appeal
REST_OF_POST_MIN_WORDS = 100

# The paywall's marker, however it's worded ("To read the rest please subscribe"). In a book the rest usually
# just follows; a line linking elsewhere with little after it means this post really is shortened, so it stays.
_PAYWALL_LINE = re.compile(r"\b(?:read|unlock)\b.{0,10}\bthe rest\b.*\bsubscri|\bsubscri.*\b(?:read|unlock)\b.{0,10}\bthe rest\b",
                           re.I)
_SHARE = re.compile(r"^https?://(?:bsky\.app/intent/|(?:www\.)?(?:twitter|x)\.com/intent/|"
                    r"(?:www\.)?facebook\.com/sharer|(?:www\.)?threads\.net/intent/)", re.I)
_SITE_SETTINGS = {"platform": str, "sites": list}
_LIST_SETTINGS = ("remove_lines", "keep_lines", "sales_links", "promo_links", "button_links")


def _normalized(words: str) -> str:
    return " ".join(words.split()).casefold().rstrip(" .!?")


def _compile_phrase(phrase: str, where: str) -> re.Pattern:
    normalized = _normalized(phrase)
    if not re.search(r"[^\W\d_]", normalized.replace("*", "")):
        raise SiteFileError(f"site rules file {where}: the phrase {phrase!r} has no words, so it could match too "
                            "much. Write the words the line must contain.")
    pattern = re.escape(normalized).replace(r"\ \*", r"(?:\W.*)?").replace(r"\*", ".*").replace(r"\#", r"\d+")
    return re.compile(pattern)


def _is_at(address: str, entries: tuple[str, ...]) -> bool:
    """Whether ``address`` starts with an entry, ignoring ``https://`` and ``www.``, and ends a word there."""
    address = re.sub(r"^(?:https?:)?//(?:www\.)?", "", address.strip(), flags=re.I).casefold()
    return any(address.startswith(entry) and (entry.endswith("/") or not address[len(entry):len(entry) + 1].isalnum())
               for entry in entries)


@dataclass
class SiteRules:
    """What one blog removes: the defaults plus its own file."""

    remove_lines: list[re.Pattern] = field(default_factory=list)
    keep_lines: list[re.Pattern] = field(default_factory=list)
    sales_links: tuple[str, ...] = ()  # buttons go, links in sentences lose the link, final sections with one go
    promo_links: tuple[str, ...] = ()  # buttons go, links in sentences stay, final sections made up of them go
    button_links: tuple[str, ...] = ()  # buttons go; nothing else changes
    remove_all_buttons: bool = False

    def is_sales_link(self, href: str) -> bool:
        return _is_at(href, self.sales_links)

    def is_promo_link(self, href: str) -> bool:
        return _is_at(href, self.promo_links)

    def sells(self, words: str) -> bool:
        line = _normalized(words)
        return any(p.fullmatch(line) for p in self.remove_lines) and not self.keeps(words)

    def keeps(self, words: str) -> bool:
        return any(p.fullmatch(_normalized(words)) for p in self.keep_lines)


def read_site_file(path: Path) -> dict:
    """A file's settings, checked. Raises ``SiteFileError`` naming the mistake."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SiteFileError(f"site rules file {path.name} can't be read: {exc}. Fix the file and build again.") from exc
    known = {*_SITE_SETTINGS, *_LIST_SETTINGS, "remove_all_buttons"}
    if unknown := sorted(set(data) - known):
        raise SiteFileError(f"site rules file {path.name}: unknown setting {', '.join(unknown)}. "
                            f"The settings are: {', '.join(sorted(known))}.")
    for name in _LIST_SETTINGS + ("sites",):
        value = data.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise SiteFileError(f"site rules file {path.name}: {name} must be a list of words in quotes.")
    if not isinstance(data.get("remove_all_buttons", False), bool):
        raise SiteFileError(f"site rules file {path.name}: remove_all_buttons must be true or false.")
    if not isinstance(data.get("platform", ""), str):
        raise SiteFileError(f"site rules file {path.name}: platform must be a name in quotes.")
    return data


def load_site_rules(name: str, sites_dir: Path | None = None) -> SiteRules:
    """The defaults plus ``<name>.toml`` if there is one. Raises ``SiteFileError`` for a file with a mistake."""
    sites_dir = sites_dir or SITES_DIR
    defaults = read_site_file(sites_dir / DEFAULTS_FILE) if (sites_dir / DEFAULTS_FILE).exists() else {}
    own = read_site_file(sites_dir / f"{name}.toml") if (sites_dir / f"{name}.toml").exists() else {}
    where = f"{name}.toml"

    def phrases(setting: str) -> list[re.Pattern]:
        return [_compile_phrase(p, where) for p in [*defaults.get(setting, []), *own.get(setting, [])]]

    def addresses(setting: str) -> tuple[str, ...]:
        return tuple(a.strip().casefold() for a in own.get(setting, []))

    return SiteRules(remove_lines=phrases("remove_lines"), keep_lines=phrases("keep_lines"),
                     sales_links=addresses("sales_links"), promo_links=addresses("promo_links"),
                     button_links=addresses("button_links"),
                     remove_all_buttons=own.get("remove_all_buttons", False))


# ---- Blocks ----

def blocks(soup) -> list:
    """Top-level content blocks of the post, ignoring whitespace between them."""
    return [c for c in soup.contents if not (isinstance(c, NavigableString) and not c.strip())]


def is_filler(block) -> bool:
    """A block that carries no content of its own: a rule, or an empty paragraph."""
    if getattr(block, "name", None) == "hr":
        return True
    return getattr(block, "name", None) == "p" and not block.get_text(strip=True) and block.find("img") is None


def previous_block(tag):
    sibling = tag.previous_sibling
    while sibling is not None and isinstance(sibling, NavigableString) and not sibling.strip():
        sibling = sibling.previous_sibling
    return sibling


def drop_dangling_end(soup) -> None:
    """After removing a trailing banner, drop rules and empty paragraphs left at the end."""
    for block in reversed(blocks(soup)):
        if is_filler(block):
            block.decompose()
        else:
            break


def _remove_block(block) -> None:
    """Remove ``block``; a removal mustn't leave two rules in a row, or a rule opening the post."""
    before, after = previous_block(block), block.find_next_sibling()
    block.extract()
    at_top = before is None or (hasattr(before, "has_attr") and before.has_attr(FEATURE_IMAGE))
    if after is not None and after.name == "hr" and (at_top or getattr(before, "name", None) == "hr"):
        after.decompose()


def _marker(soup, block, words: str, kind: str):
    attrs = {CALL_TO_ACTION_REMOVED: words, CALL_TO_ACTION_REMOVED_AS: kind}
    link = block.find("a", href=True)
    mark = block.find("span", attrs={CALL_TO_ACTION: True})
    if url := (link["href"] if link else mark[CALL_TO_ACTION] if mark else None):
        attrs[CALL_TO_ACTION_REMOVED_URL] = url
    return soup.new_tag("span", attrs=attrs)


# ---- The rules, in the order the base processor applies them ----

def is_sales_button(button, rules: SiteRules) -> bool:
    link = button.find("a", href=True)
    return rules.remove_all_buttons or bool(link and (
        _SHARE.match(link["href"]) or rules.is_sales_link(link["href"]) or rules.is_promo_link(link["href"])
        or _is_at(link["href"], rules.button_links)))


def remove_buttons(soup, rules: SiteRules, report: bool = True) -> None:
    """Remove buttons that sell or share (canonical ``<p class="button">``), reported unless told otherwise."""
    markers = []
    for button in soup.find_all("p", class_="button"):
        if not is_sales_button(button, rules):
            continue
        if report:
            words = " ".join(button.get_text(" ", strip=True).split())
            markers.append(_marker(soup, button, words, "button"))
            _remove_block(button)
        else:
            button.decompose()
    if markers:
        drop_dangling_end(soup)
        soup.extend(markers)


def remove_final_sections(soup, rules: SiteRules, heading: re.Pattern | None = None) -> None:
    """Drop a post's final section, after its last <hr>, when it has a sales link or is made up of
    promo links, or is headed by ``heading``, and any such section that leaves last. Long sections
    stay, as do sections mixing writing with a promo. Markers are left for the report."""
    markers = []
    while marker := _remove_final_section(soup, rules, heading):
        markers.append(marker)
    soup.extend(markers)


def _remove_final_section(soup, rules: SiteRules, heading: re.Pattern | None):
    parts = blocks(soup)
    hrs = [i for i, block in enumerate(parts) if getattr(block, "name", None) == "hr"]
    if not hrs:
        return None
    section = [block for block in parts[hrs[-1] + 1:] if hasattr(block, "find_all")]
    hrefs = [a["href"] for block in section for a in block.find_all("a", href=True)]
    links = [href for href in hrefs if rules.is_sales_link(href)] + [href for href in hrefs if rules.is_promo_link(href)]
    text = " ".join(" ".join(block.get_text(" ", strip=True).split()) for block in section).strip()
    if not (any(rules.is_sales_link(href) for href in hrefs) or _is_promo_section(section, rules, heading)) \
            or len(text.split()) > CLOSING_APPEAL_MAX_WORDS:
        return None
    for block in [parts[hrs[-1]], *section]:
        block.extract()
    drop_dangling_end(soup)
    attrs = {CALL_TO_ACTION_REMOVED: text}
    if links:
        attrs[CALL_TO_ACTION_REMOVED_URL] = links[0]
    return soup.new_tag("span", attrs=attrs)


def _is_promo_section(section: list, rules: SiteRules, heading: re.Pattern | None) -> bool:
    """A section that's only a promotion, with no writing mixed in: every paragraph links to it."""
    def has_link(block) -> bool:
        return any(rules.is_promo_link(a["href"]) for a in block.find_all("a", href=True))

    named = heading is not None and any(block.name in ("h2", "h3", "h4") and heading.search(block.get_text())
                                        for block in section)
    paragraphs = [block for block in section if block.name == "p" and block.get_text(strip=True)]
    return (named or any(has_link(block) for block in section)) and all(has_link(p) for p in paragraphs)


def unlink_sales_links(soup, rules: SiteRules) -> None:
    """Sales links inside text: keep the words, mark them for the report."""
    for link in soup.find_all("a", href=True):
        if rules.is_sales_link(link["href"]) and link.find("img") is None:
            mark_call_to_action(soup, link)


def remove_sales_lines(soup, rules: SiteRules) -> None:
    """Remove lines that only sell: a listed phrase, a paywall marker, or nothing but call-to-action
    links. Someone else's words (quotes, captions) and images stay. Each is reported."""
    markers = []
    for block in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
        if block.find_parent(["blockquote", "figure"]) is not None or block.find("img") is not None:
            continue
        words = " ".join(block.get_text(" ", strip=True).split())
        if words and _only_sells(block, words, rules):
            markers.append(_marker(soup, block, words, "sales line"))
            _remove_block(block)
    if markers:
        drop_dangling_end(soup)
        soup.extend(markers)


def _only_sells(block, words: str, rules: SiteRules) -> bool:
    if rules.keeps(words):
        return False
    if len(words.split()) <= LINE_MAX_WORDS and rules.sells(words):
        return True
    if _all_marked(block):
        return True
    if len(words.split()) > LINE_MAX_WORDS or not _PAYWALL_LINE.search(words):
        return False
    return block.find("a", href=True) is None or _words_after(block) >= REST_OF_POST_MIN_WORDS


def _words_after(block) -> int:
    return sum(len(sibling.get_text(" ").split()) for sibling in block.find_next_siblings())


def _all_marked(block) -> bool:
    """Whether every word of ``block`` was a call-to-action link (unwrapped and marked)."""
    marks = block.find_all("span", attrs={CALL_TO_ACTION: True})
    rest = "".join(s for s in block.strings if s.find_parent("span", attrs={CALL_TO_ACTION: True}) is None)
    return bool(marks) and not rest.strip()
