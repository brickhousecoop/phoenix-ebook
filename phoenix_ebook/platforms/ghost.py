"""Ghost platform: posts via the Ghost Admin API, authenticated with a short-lived JWT."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from phoenix_ebook.models import Author, Post
from phoenix_ebook.platforms.base import Platform, register_platform


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(admin_key: str) -> str:
    """Sign a 5-minute Admin API JWT from an ``id:secret`` Admin API key (secret is hex)."""
    key_id, key_secret = admin_key.strip().split(":")
    header = json.dumps({"alg": "HS256", "typ": "JWT", "kid": key_id}).encode()
    payload = json.dumps(
        {"iat": int(time.time()), "exp": int(time.time()) + 300, "aud": "/admin/"}
    ).encode()
    signing_input = f"{_b64url(header)}.{_b64url(payload)}"
    signature = hmac.new(bytes.fromhex(key_secret), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


@register_platform
class GhostPlatform(Platform):
    """Fetches posts (``formats=html``) from a Ghost site's Admin API.

    Publication times are converted from UTC to the site's configured timezone,
    read once per site from ``/ghost/api/admin/site/``.
    """

    name = "ghost"

    def __init__(self) -> None:
        self._timezones: dict[str, ZoneInfo | None] = {}  # site url -> timezone

    def fetch_post(self, session: requests.Session, url: str, secret: str, slug: str) -> Post:
        token = make_token(secret)
        r = session.get(
            f"{url.rstrip('/')}/ghost/api/admin/posts/slug/{slug}/",
            params={"formats": "html"},
            headers={"Authorization": f"Ghost {token}"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(data["errors"])
        post = data["posts"][0]

        authors = [
            Author(name=a["name"], url=a.get("url") or None)
            for a in (post.get("authors") or []) if a.get("name")
        ]

        return Post(
            slug=post.get("slug") or slug,
            title=post.get("title") or slug,
            html=post.get("html") or "",
            authors=authors,
            published_at=self._localize(session, url, post.get("published_at")),
            feature_image=post.get("feature_image") or None,
            feature_image_alt=post.get("feature_image_alt") or None,
            feature_image_caption=post.get("feature_image_caption") or None,
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
