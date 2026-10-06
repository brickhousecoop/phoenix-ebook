"""Repairing links in posts, whatever the platform (#22).

Writers mistype addresses and editors rename sections, so posts contain links
an EPUB can't hold: in-post ``#`` links to sections that don't exist, addresses
with spaces, quotes or missing schemes, and links with no text. Each is repaired
when the intent is clear, otherwise unlinked (the words stay). Every change is
marked (``phoenix_ebook.canonical``) for the build report, since a repair can be
wrong.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup

from phoenix_ebook.canonical import LINK_REASON, LINK_REMOVED, LINK_REPAIRED, LINK_UNLINKED

_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
_DOMAIN_PATH = re.compile(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.I)  # "bsky.app/profile/x"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_TRIM = " \t\n\r ​\"'“”‘’«»<>"
# ASCII characters an address may contain as they are (RFC 3986, minus "[]", which only IPv6 hosts use).
# Non-ASCII letters are fine too: EPUB links are IRIs.
_URL_ASCII_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#@!$&'()*+,;=%")


def _encode_invalid(href: str) -> str:
    return "".join(c if ord(c) > 127 or c in _URL_ASCII_OK else quote(c, safe="") for c in href)


def remove_empty_headings_and_ids(soup: BeautifulSoup) -> None:
    """Drop headings with no text or image (editor spacers) and ``id=""`` (invalid). Not reported."""
    for heading in soup.find_all(_HEADINGS):
        if not heading.get_text(strip=True) and heading.find("img") is None:
            heading.decompose()
    for tag in soup.find_all(id=True):
        if not tag["id"].strip():
            del tag["id"]


def repair_links(soup: BeautifulSoup) -> None:
    """Repair, unlink or remove broken links; mark each change for the report."""
    for anchor in soup.find_all("a", attrs={"name": True}):  # old-style targets: <a name="note-3">
        if anchor["name"].strip() and not anchor.get("id"):
            anchor["id"] = anchor["name"].strip()
        del anchor["name"]
    _simplify_ids(soup)
    for link in soup.find_all("a", href=True):
        _repair_address(soup, link)
    ids = {tag["id"] for tag in soup.find_all(id=True)}
    for link in soup.find_all("a", href=re.compile(r"^#.")):
        if link["href"][1:] not in ids:
            _repair_section_link(soup, link, ids)
    for link in soup.find_all("a", href=True):
        if not link.get_text(strip=True) and link.find(True) is None:
            marker = soup.new_tag("span", attrs={LINK_REMOVED: _original(link)})
            link.replace_with(marker)


# ---------------------------------------------------------------- helpers

def _original(link) -> str:
    return link.get(LINK_REPAIRED, link["href"])


def _repaired(link, href: str, reason: str) -> None:
    if href == link["href"]:
        return
    if LINK_REPAIRED not in link.attrs:
        link[LINK_REPAIRED] = link["href"]
    link[LINK_REASON] = f"{link[LINK_REASON]}; {reason}" if LINK_REASON in link.attrs else reason
    link["href"] = href


def _unlink(soup, link, reason: str) -> None:
    span = soup.new_tag("span", attrs={LINK_UNLINKED: _original(link), LINK_REASON: reason})
    span.extend(list(link.contents))
    link.replace_with(span)


# Letters with no ASCII decomposition, spelled out.
_SPELLED = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss", "ø": "o", "Ø": "O", "ł": "l",
                          "Ł": "L", "đ": "d", "Đ": "D", "þ": "th", "Þ": "Th"})


def slugify(text: str) -> str:
    """'Día de los Muertos — “Live”' -> 'dia-de-los-muertos-live' (ASCII, for ids)."""
    ascii_text = unicodedata.normalize("NFKD", unquote(text).translate(_SPELLED)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _key(text: str) -> str:
    """For matching: letters and digits only, case-folded, quotes and accents ignored."""
    return re.sub(r"[^a-z0-9]", "", slugify(text))


def _unique_id(soup, base: str, taken: set) -> str:
    base = base or "section"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def _simplify_ids(soup) -> None:
    """Give non-ASCII ids (Ghost makes ids like '%E2%80%9Cquote' or 'día') plain ones, and follow links to them."""
    taken = {tag["id"] for tag in soup.find_all(id=True)}
    renamed = {}
    for tag in soup.find_all(id=True):
        old = tag["id"]
        if not _SAFE_ID.match(old):
            new = _unique_id(soup, slugify(old), taken)
            renamed[old] = renamed[unquote(old)] = renamed[quote(unquote(old), safe="")] = new
            tag["id"] = new
    if not renamed:
        return
    for link in soup.find_all("a", href=re.compile(r"^#.")):
        target = renamed.get(link["href"][1:]) or renamed.get(unquote(link["href"][1:]))
        if target:
            _repaired(link, f"#{target}", "section id simplified")


def _repair_address(soup, link) -> None:
    href = link["href"]
    trimmed = href.strip(_TRIM)
    if trimmed != href:
        _repaired(link, trimmed, "spaces or quotes trimmed")
    href = link["href"]
    if not href:
        _unlink(soup, link, "no address")
        return
    if re.match(r"^mailto:\s+", href, re.I):
        _repaired(link, re.sub(r"^mailto:\s+", "mailto:", href, flags=re.I), "space after mailto: removed")
    elif re.match(r"^https?//", href, re.I):
        _repaired(link, re.sub(r"^(https?)//", r"\1://", href, flags=re.I), "missing ':' added")
    elif href.startswith("#") and _DOMAIN_PATH.match(href[1:]) and "/" in href:
        _repaired(link, "https://" + href[1:], "a web address written as a section link")
    elif not href.startswith(("#", "/", ".")) and not _SCHEME.match(href) and _DOMAIN_PATH.match(href):
        _repaired(link, "https://" + href, "https:// added")
    href = link["href"]
    if href.startswith("#"):
        return  # section links: see _repair_section_link
    if re.search(r"\s", href) or (not _SCHEME.match(href) and not href.startswith(("/", "."))):
        _unlink(soup, link, "not a web address")
        return
    encoded = _encode_invalid(href)
    if encoded != href:
        _repaired(link, encoded, "characters encoded")


def _repair_section_link(soup, link, ids: set) -> None:
    """An in-post link to a section that doesn't exist: point it at the section it meant, or unlink it."""
    fragment = link["href"][1:]
    want = _key(fragment)
    text = _key(link.get_text())

    same_id = [i for i in ids if _key(i) == want]
    if len(same_id) == 1:
        _repaired(link, f"#{same_id[0]}", "matched a section id")
        return

    headings = [h for h in soup.find_all(_HEADINGS) if h.get_text(strip=True)]
    def unique(matches):
        return matches[0] if len(matches) == 1 else None
    heading = (unique([h for h in headings if _key(h.get_text()) in (want, text)])
               or unique([h for h in headings if len(text) >= 8 and (_key(h.get_text()).startswith(text)
                                                                     or text.startswith(_key(h.get_text())))]))
    if heading is not None:
        if not heading.get("id"):
            heading["id"] = _unique_id(soup, slugify(heading.get_text()), ids)
        _repaired(link, f"#{heading['id']}", f"matched the heading \"{' '.join(heading.get_text().split())[:80]}\"")
        return

    keys = {_key(i): i for i in ids}
    close = difflib.get_close_matches(want, list(keys), n=2, cutoff=0.9) if want else []
    if len(close) == 1 or (len(close) == 2 and difflib.SequenceMatcher(None, want, close[0]).ratio()
                           > difflib.SequenceMatcher(None, want, close[1]).ratio() + 0.02):
        _repaired(link, f"#{keys[close[0]]}", "matched the closest section id")
        return

    _unlink(soup, link, "points to a section that isn't in this post")
