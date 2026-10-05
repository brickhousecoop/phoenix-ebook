from __future__ import annotations

from bs4 import BeautifulSoup

from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import HtmlProcessor, register_processor


@register_processor
class FlamingHydraProcessor(HtmlProcessor):
    name = "flaminghydra"

    def clean(self, soup: BeautifulSoup, post: Post) -> None:
        super().clean(soup, post)
        for hr in soup.find_all("hr"):
            next_sib = hr.find_next_sibling()
            if next_sib and next_sib.name == "p":
                link = next_sib.find("a", href=lambda h: h and "#comments" in h)
                if link:
                    next_sib.decompose()
                    hr.decompose()
                    break
