"""The Phoenix Library Service section that ends "About This Book" in every book (#50).

Its words are data: ``credits.html`` here, with its images next to it. ``credits_section()`` turns it
into the page's last ``<section>`` and lists the images the book must carry.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Comment

CREDITS_DIR = Path(__file__).parent
CREDITS_FILE = CREDITS_DIR / "credits.html"
IMAGE_PREFIX = "images/credits-"  # where the section's images go in the book


@dataclass
class Credits:
    html: str  # the <section>, image addresses pointing into the book
    images: list[Path]  # files to add to the book, each as IMAGE_PREFIX + its name
    described: int  # images with alt text, for the accessibility summary
    decorative: int  # images with empty alt text


def credits_section(path: Path = CREDITS_FILE) -> Credits:
    """The credits as a section of the About page, with its images' addresses in the book."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    images, described, decorative = [], 0, 0
    for img in soup.find_all("img"):
        source = path.parent / img["src"]
        images.append(source)
        img["src"] = IMAGE_PREFIX + source.name
        if img.get("alt", "").strip():
            described += 1
        else:
            decorative += 1
    return Credits(f'<section class="phoenix-credits">\n{soup.decode().strip()}\n</section>',
                   images, described, decorative)
