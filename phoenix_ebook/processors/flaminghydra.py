from __future__ import annotations

from bs4 import BeautifulSoup

from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import HtmlProcessor, register_processor


@register_processor
class FlamingHydraProcessor(HtmlProcessor):
    name = "flaminghydra"

    def display_title(self, post: Post) -> str:
        author = post.authors[0] if post.authors else self._scrape_author(post.html)
        return f"{post.title} — {author}" if author else post.title

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

    @staticmethod
    def _scrape_author(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        el = soup.find(class_="gh-post-authors") or soup.find(
            "a", href=lambda h: h and "/author/" in h
        )
        return el.get_text(strip=True) if el else None
