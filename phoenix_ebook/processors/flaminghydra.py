"""Flaming Hydra (flaminghydra.com) quirks."""
from __future__ import annotations

from bs4 import BeautifulSoup

from phoenix_ebook.models import Post
from phoenix_ebook.processors.base import HtmlProcessor, register_processor


@register_processor
class FlamingHydraProcessor(HtmlProcessor):
    """Flaming Hydra: drops the comments footer, and labels contents entries "Title — Author"."""

    name = "flaminghydra"

    def display_title(self, post: Post) -> str:
        """Collections mix many writers, so the contents show who wrote each piece."""
        names = [a.name for a in post.authors]
        if not names:
            return post.title
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"{post.title} — {joined}"

    def clean(self, soup: BeautifulSoup, post: Post) -> None:
        super().clean(soup, post)
        # Posts end with an <hr> and a line inviting readers to the site's comments
        # ("You may Shred in the Comments Section", linking to #comments). It means
        # nothing in a book, so drop the first such <hr> + paragraph pair.
        for hr in soup.find_all("hr"):
            next_sib = hr.find_next_sibling()
            if next_sib and next_sib.name == "p":
                link = next_sib.find("a", href=lambda h: h and "#comments" in h)
                if link:
                    next_sib.decompose()
                    hr.decompose()
                    break
