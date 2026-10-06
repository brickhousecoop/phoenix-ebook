"""Ghost platform: posts via the Ghost Admin API, authenticated with a short-lived JWT."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from bs4 import BeautifulSoup

from phoenix_ebook.errors import AuthError, PostNotFound, SourceUnreachable, explain_response
from phoenix_ebook.models import Author, Post
from phoenix_ebook.platforms.base import Platform, register_platform


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(admin_key: str) -> str:
    """Sign a 5-minute Admin API JWT from an ``id:secret`` Admin API key (secret is hex)."""
    parts = admin_key.strip().split(":")
    try:
        key_id, key_secret = parts
        secret_bytes = bytes.fromhex(key_secret)
    except ValueError:
        raise AuthError("The Ghost Admin API key must look like id:secret (copy it from Ghost Admin → "
                        "Settings → Integrations → your custom integration → Admin API key).") from None
    header = json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}).encode()
    payload = json.dumps(
        {"iat": int(time.time()), "exp": int(time.time()) + 300, "aud": "/admin/"}
    ).encode()
    signing_input = f"{_b64url(header)}.{_b64url(payload)}"
    signature = hmac.new(secret_bytes, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


@register_platform
class GhostPlatform(Platform):
    """Fetches posts (``formats=html``) from a Ghost site's Admin API.

    Publication times are converted from UTC to the site's configured timezone,
    read once per site from ``/ghost/api/admin/site/``.
    """

    name = "ghost"
    default_processor = "generic"

    def normalize_html(self, html: str) -> str:
        """Ghost's dialect -> canonical chapter HTML (docs/canonical-html.md).

        Drops the editor's ``kg-*`` card classes and converts Markdown-card
        (markdown-it) footnotes to canonical notes.
        """
        if "kg-" not in html and "footnote" not in html:
            return html
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(class_=True):
            classes = [c for c in tag.get("class", []) if not c.startswith("kg-")]
            if classes:
                tag["class"] = classes
            else:
                del tag["class"]
        _convert_markdown_footnotes(soup)
        return str(soup)

    def canonical_image_url(self, url: str) -> str:
        """Also ignore Ghost's resized variants: '…/content/images/size/w1000/2026/a.png' -> '…/content/images/2026/a.png'."""
        canonical = super().canonical_image_url(url)
        return re.sub(r"/size/w\d+(?:h\d+)?/", "/", canonical, count=1)

    def __init__(self) -> None:
        self._timezones: dict[str, ZoneInfo | None] = {}  # site url -> timezone

    def fetch_post(self, session: requests.Session, url: str, secret: str, slug: str) -> Post:
        token = make_token(secret)
        host = urlsplit(url).netloc or url
        try:
            r = session.get(
                f"{url.rstrip('/')}/ghost/api/admin/posts/slug/{slug}/",
                params={"formats": "html"},
                headers={"Authorization": f"Ghost {token}"},
                timeout=15,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise SourceUnreachable(f"can't reach {host}: {type(exc).__name__}. Check the --url and your "
                                    "network connection.") from exc
        data = _ghost_json(r)
        if r.status_code == 404 and data is not None:
            raise PostNotFound(host, [slug])
        if r.status_code == 401 or (r.status_code == 403 and data is not None):
            raise _auth_error(r, host, url)
        if not 200 <= r.status_code < 300:
            why = explain_response(r)
            raise SourceUnreachable(f"can't reach {host} (HTTP {r.status_code})" + (f": {why}" if why else "")
                                    + ("" if why else ". " + _NOT_GHOST_HINT.format(host=host)))
        if data is None or not data.get("posts"):
            raise SourceUnreachable(f"{host} didn't answer like a Ghost Admin API. " + _NOT_GHOST_HINT.format(host=host))
        post = data["posts"][0]

        authors = [
            Author(name=a["name"], url=a.get("url") or None)
            for a in (post.get("authors") or []) if a.get("name")
        ]

        return Post(
            slug=post.get("slug") or slug,
            title=post.get("title") or slug,
            html=self.normalize_html(post.get("html") or ""),
            authors=authors,
            published_at=self._localize(session, url, post.get("published_at")),
            feature_image=post.get("feature_image") or None,
            feature_image_alt=post.get("feature_image_alt") or None,
            feature_image_caption=self.normalize_html(post.get("feature_image_caption") or "") or None,
            raw=post,
        )

    def _localize(self, session: requests.Session, url: str, timestamp: str | None) -> str | None:
        """Convert a UTC timestamp to the site's timezone, so dates match the site."""
        if not timestamp:
            return timestamp
        tz = self._site_timezone(session, url)
        if tz is None:
            return timestamp
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(tz).isoformat()

    def _site_timezone(self, session: requests.Session, url: str) -> ZoneInfo | None:
        cache = self._timezones
        if url not in cache:
            try:
                r = session.get(f"{url.rstrip('/')}/ghost/api/admin/site/", timeout=15)
                r.raise_for_status()
                cache[url] = ZoneInfo(r.json()["site"]["timezone"])
            except (requests.RequestException, KeyError, ValueError, ZoneInfoNotFoundError):
                cache[url] = None  # fall back to UTC dates
        return cache[url]


_NOT_GHOST_HINT = ("Is --url the site's Ghost address (often https://<site>.ghost.io, as shown in "
                   "Ghost Admin), not the public website ({host})?")


def _ghost_json(r) -> dict | None:
    """The response's JSON object, or None if it isn't JSON (e.g. an HTML page or a proxy message)."""
    if "json" not in (r.headers.get("Content-Type") or "").lower():
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _auth_error(r, host: str, url: str) -> AuthError:
    redirected = [h for h in getattr(r, "history", []) if 300 <= h.status_code < 400]
    if redirected:
        final = urlsplit(r.url)
        return AuthError(f"{host} redirected to {final.scheme}://{final.netloc}, and the key isn't sent across "
                         f"redirects. Use --url {final.scheme}://{final.netloc} instead.")
    why = explain_response(r)
    return AuthError(f"Ghost rejected the Admin API key for {host} (HTTP {r.status_code})"
                     + (f": {why}" if why else "")
                     + f". Check the key, or store a new one: build_epub.py --set-secret --platform ghost --url {url}")


def _convert_markdown_footnotes(soup) -> None:
    """markdown-it footnotes (Ghost's Markdown card) -> canonical notes.

    Only the exact markdown-it structure is converted: a ``sup.footnote-ref`` link
    to an ``li.footnote-item`` inside this post's ``section.footnotes``.
    """
    section = soup.find("section", class_="footnotes")
    if section is None:
        return
    items = [li for li in section.find_all("li", class_="footnote-item") if li.get("id")]
    note_ids = {li["id"] for li in items}
    if not note_ids:
        return

    for sup in soup.find_all("sup", class_="footnote-ref"):
        link = sup.find("a", href=True)
        if link is None or link["href"][1:] not in note_ids or not link["href"].startswith("#"):
            continue
        link["epub:type"] = "noteref"
        link["role"] = "doc-noteref"
        link.string = re.sub(r"^\[(.+)\]$", r"\1", link.get_text().strip())  # "[1]" -> "1"
        sup.unwrap()  # core.css already sets noterefs as superscript

    for rule in soup.find_all("hr", class_="footnotes-sep"):
        rule.decompose()
    section.attrs = {"epub:type": "endnotes", "role": "doc-endnotes"}
    heading = soup.new_tag("h3")
    heading.string = "Notes"
    section.insert(0, heading)
    for ol in section.find_all("ol", class_="footnotes-list"):
        del ol["class"]
    for li in items:
        del li["class"]
        li["epub:type"] = "endnote"  # no ARIA role: doc-endnote is deprecated (items of doc-endnotes)
    for backlink in section.find_all("a", class_="footnote-backref"):
        del backlink["class"]
        backlink["role"] = "doc-backlink"
