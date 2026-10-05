from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import requests

from phoenix_ebook.models import Post
from phoenix_ebook.platforms.base import Platform, register_platform


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(admin_key: str) -> str:
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
    name = "ghost"

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

        authors = [a.get("name") for a in (post.get("authors") or []) if a.get("name")]

        return Post(
            slug=post.get("slug") or slug,
            title=post.get("title") or slug,
            html=post.get("html") or "",
            authors=authors,
            published_at=post.get("published_at"),
            raw=post,
        )
