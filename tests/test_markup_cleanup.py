"""Ghost editor / web-only markup cleanup (#3)."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import phoenix_ebook.processors  # noqa: F401 — registers site processors
from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import get_processor


def clean(html: str, processor: str = "generic") -> str:
    soup = BeautifulSoup(html, "html.parser")
    get_processor(processor).clean(soup, Post(slug="p", title="t", html=html))
    return str(soup)


@pytest.mark.parametrize("html, expected", [
    # loading/decoding removed; width/height kept (Ghost's kg-* classes: see test_platforms.py)
    ('<figure><img class="photo" loading="lazy" decoding="async" src="a.jpg" width="10" height="5" alt="x"/></figure>',
     '<figure><img alt="x" class="photo" height="5" src="a.jpg" width="10"/></figure>'),
    ('<p class="">t</p>', '<p>t</p>'),
    # inline styles and comments
    ('<p style="color:red">a<!--members-only-->b</p>', "<p>ab</p>"),
    # leading / trailing <br> in blocks
    ("<p><br/><em>x</em></p>", "<p><em>x</em></p>"),
    ("<p> <br/> <br/>x<br/> </p>", "<p>  x </p>"),
    ("<li><br/>item</li>", "<li>item</li>"),
    ("<figcaption>cap<br/></figcaption>", "<figcaption>cap</figcaption>"),
    ("<blockquote><br/>q</blockquote>", "<blockquote>q</blockquote>"),
    # nested bold collapses
    ('<figcaption><b><strong style="white-space: pre-wrap;">Cap</strong></b></figcaption>',
     "<figcaption><strong>Cap</strong></figcaption>"),
    ("<strong><b>x</b></strong>", "<strong>x</strong>"),
    ("<b><strong><b>deep</b></strong></b>", "<strong>deep</strong>"),
    ('<u><b><strong class="underline">PD</strong></b></u>', '<u><strong class="underline">PD</strong></u>'),
])
def test_cleanup(html, expected):
    assert clean(html) == expected


@pytest.mark.parametrize("html", [
    "<p>a<br/>b</p>",                       # <br> inside text stays
    "<b>a <strong>b</strong></b>",          # mixed content isn't collapsed
    "<b>plain</b>",
])
def test_left_alone(html):
    assert clean(html) == html


def test_scripts_iframes_and_styles_removed():
    assert clean("<p>a</p><script>x()</script><iframe src='y'></iframe><style>p{}</style>") == "<p>a</p>"


def test_site_processor_inherits_cleanup():
    assert clean('<p style="x">a<!--c--></p>', "flaminghydra") == "<p>a</p>"


def test_generic_cleanup_leaves_other_platforms_classes_alone():
    assert clean('<p class="kg-x keep">t</p>') == '<p class="kg-x keep">t</p>'  # kg-* is Ghost's, handled there
