"""Platforms normalize to canonical chapter HTML; processors are chosen per platform (ADR 0001, #19)."""
from __future__ import annotations

import json

import pytest

import build_epub
from conftest import FakeResponse, FakeSession
from phoenix_ebook.models import Post
from phoenix_ebook.platforms.base import Platform, register_platform, _REGISTRY as PLATFORMS
from phoenix_ebook.platforms.ghost import GhostPlatform
from phoenix_ebook.processors.base import HtmlProcessor, register_processor, select_processor, _REGISTRY as PROCESSORS

SITE = "https://site.ghost.io"
KEY = "0123456789abcdef01234567:" + "ab" * 32


# ---------------------------------------------------------------- Ghost normalization

@pytest.mark.parametrize("html, expected", [
    ('<figure class="kg-card kg-image-card kg-card-hascaption"><img class="kg-image" src="a.jpg"/></figure>',
     '<figure><img src="a.jpg"/></figure>'),
    ('<p class="kg-x keep">t</p>', '<p class="keep">t</p>'),
    ('<p class="keep">untouched</p>', '<p class="keep">untouched</p>'),
])
def test_ghost_normalize_strips_kg_classes(html, expected):
    assert GhostPlatform().normalize_html(html) == expected


def test_ghost_fetch_post_returns_canonical_html():
    body = {"posts": [{"slug": "s", "title": "S", "authors": [],
                       "html": '<figure class="kg-card kg-image-card"><img class="kg-image" src="a.jpg"/></figure>',
                       "feature_image": "https://x/f.png",
                       "feature_image_caption": '<span class="kg-x">Cap</span>'}]}
    session = FakeSession({f"{SITE}/ghost/api/admin/posts/slug/s/": [FakeResponse(200, json.dumps(body).encode(), "application/json")]})
    post = GhostPlatform().fetch_post(session, SITE, KEY, "s")
    assert "kg-" not in post.html and "kg-" not in post.feature_image_caption
    assert post.feature_image_caption == "<span>Cap</span>"


@pytest.mark.parametrize("url, expected", [
    ("https://x/content/images/size/w1000/2026/08/a.png?v=2", "https://x/content/images/2026/08/a.png"),
    ("https://x/content/images/size/w600h400/2026/08/a.png", "https://x/content/images/2026/08/a.png"),
    ("https://x/content/images/2026/08/a.png#f", "https://x/content/images/2026/08/a.png"),
])
def test_ghost_canonical_image_url(url, expected):
    assert GhostPlatform().canonical_image_url(url) == expected


# ---------------------------------------------------------------- a second platform

@pytest.fixture
def other_platform():
    @register_platform
    class OtherPlatform(Platform):
        name = "other-test"
        default_processor = "other-default"

        def fetch_post(self, session, url, secret, slug):
            return Post(slug=slug, title=slug, html=self.normalize_html('<p class="kg-card">x</p>'))

    @register_processor
    class OtherDefault(HtmlProcessor):
        name = "other-default"
        platform = "other-test"

    yield OtherPlatform
    PLATFORMS.pop("other-test", None)
    PROCESSORS.pop("other-default", None)


def test_other_platform_gets_none_of_ghosts_rules(other_platform):
    platform = other_platform()
    assert platform.fetch_post(None, "https://o.test", "k", "s").html == '<p class="kg-card">x</p>'
    assert platform.canonical_image_url("https://o.test/size/w1000/a.png?v=1") == "https://o.test/size/w1000/a.png"


def test_other_platform_default_processor(other_platform):
    assert build_epub._default_processor_for("other-test", "https://flaminghydra.other.test") == "other-default"


# ---------------------------------------------------------------- processor selection (same answers as before)

@pytest.mark.parametrize("url, expected", [
    ("https://flaminghydra.ghost.io", "flaminghydra"),
    ("https://flaminghydra.com", "flaminghydra"),
    ("https://www.flaminghydra.com", "flaminghydra"),
    ("https://another.ghost.io", "generic"),
])
def test_ghost_processor_selection(url, expected):
    assert build_epub._default_processor_for("ghost", url) == expected


def test_explicit_processor_wins():
    args = build_epub.build_parser().parse_args(["--processor", "generic", "s"])
    assert build_epub._spec_from_args(args).source.processor == "generic"


def test_select_processor_requires_matching_platform():
    assert select_processor("not-ghost", "flaminghydra.com", "fallback") == "fallback"
