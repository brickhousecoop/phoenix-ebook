from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image

from phoenix_ebook.models import BookSpec, Post
from phoenix_ebook.processors.base import HtmlProcessor


CSS = """
body { font-family: Georgia, serif; line-height: 1.6; margin: 1em; }
img { max-width: 100%; height: auto; display: block; margin: 1em 0; }
h1, h2, h3 { font-family: sans-serif; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-width: 100%; height: auto; }
"""


def media_type_for_ext(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(ext.lower(), "image/jpeg")


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name).strip("_") or "item"


def wrap_text_as_html(title: str, text: str) -> str:
    if text.strip().startswith("<"):
        return text
    paragraphs = "".join(f"<p>{line}</p>" for line in text.splitlines() if line.strip())
    return f"<h1>{title}</h1>\n{paragraphs}"


def _add_page(book, css, title, file_name, content, lang):
    if not content or not content.strip():
        content = f"<p>({title} — content placeholder)</p>"
    page = epub.EpubHtml(title=title, file_name=file_name, lang=lang)
    page.content = content
    page.add_item(css)
    book.add_item(page)
    return page


def _load_or_placeholder(path, title, placeholder_text):
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip()
    return f"<h1>{title}</h1>\n<p>{placeholder_text}</p>"


def build(
    spec: BookSpec,
    posts: Iterable[Post],
    processor: HtmlProcessor,
    *,
    session: requests.Session | None = None,
    image_base_url: str = "",
) -> str:
    """Assemble an EPUB from already-fetched posts. Returns output path."""
    session = session or requests.Session()

    book = epub.EpubBook()
    book.set_identifier(spec.isbn or str(uuid.uuid4()))
    book.set_title(spec.title)
    book.set_language(spec.lang)

    if spec.author:
        book.add_metadata("DC", "creator", spec.author)
    if spec.publisher:
        book.add_metadata("DC", "publisher", spec.publisher)
    if spec.description:
        book.add_metadata("DC", "description", spec.description)
    if spec.pub_date:
        book.add_metadata("DC", "date", spec.pub_date)
    book.add_metadata("DC", "rights", "© All rights reserved.")

    css = epub.EpubItem(uid="style", file_name="style/style.css", media_type="text/css", content=CSS)
    book.add_item(css)

    pages = []
    image_items: dict[str, epub.EpubItem] = {}

    # ---- Cover ----
    if spec.cover:
        cover_path = Path(spec.cover)
        cover_bytes = cover_path.read_bytes()
        try:
            img = Image.open(io.BytesIO(cover_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            cover_bytes = buf.getvalue()
            cover_ext = ".jpg"
        except Exception:
            cover_ext = cover_path.suffix.lower() or ".jpg"
            if cover_ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
                cover_ext = ".jpg"

        cover_fname = f"cover{cover_ext}"
        cover_item = epub.EpubItem(
            uid="cover-image",
            file_name=f"images/{cover_fname}",
            media_type=media_type_for_ext(cover_ext),
            content=cover_bytes,
        )
        cover_item.properties = {"cover-image"}
        book.add_item(cover_item)

        book.add_metadata(None, "meta", "", {"name": "cover", "content": "cover-image"})

        cover_page = _add_page(
            book, css, "Cover", "cover.xhtml",
            f'<div class="cover"><img src="images/{cover_fname}" alt="Cover"/></div>',
            spec.lang,
        )
        cover_page.id = "cover"
        pages.append(cover_page)

        book.add_metadata(
            None, "reference", "",
            {"type": "cover", "title": "Cover", "href": "cover.xhtml"},
        )

    # ---- Front matter ----
    for file_attr, page_title, file_name, placeholder in [
        (spec.copyright_file, "Copyright", "copyright.xhtml",
         "[Copyright placeholder — replace with publication copyright notice.]"),
        (spec.imprint_file, "Imprint", "imprint.xhtml",
         "[Imprint placeholder — publisher, edition, printing history, etc.]"),
        (spec.foreword_file, "Foreword", "foreword.xhtml",
         "[Foreword placeholder — introductory remarks by a guest writer.]"),
    ]:
        html = _load_or_placeholder(file_attr, page_title, placeholder)
        pages.append(_add_page(book, css, page_title, file_name, html, spec.lang))

    if spec.intro_file:
        intro_text = Path(spec.intro_file).read_text(encoding="utf-8").strip()
        if intro_text:
            pages.append(_add_page(
                book, css, "Introduction", "intro.xhtml",
                wrap_text_as_html("Introduction", intro_text),
                spec.lang,
            ))

    # ---- Chapters ----
    for post in posts:
        chapter_slug = sanitize_filename(post.slug)
        soup = BeautifulSoup(post.html, "html.parser")
        processor.clean(soup, post)
        display_title = processor.display_title(post)

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue
            src = urljoin(image_base_url, src) if image_base_url else src
            parsed = urlparse(src)
            ext = os.path.splitext(parsed.path.split("?")[0])[1].lower() or ".jpg"
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
                ext = ".jpg"

            uid = hashlib.sha1(src.encode()).hexdigest()[:12]
            fname = f"{chapter_slug}_{uid}{ext}"

            if src not in image_items:
                try:
                    data = session.get(src, timeout=15).content
                    item = epub.EpubItem(
                        uid=f"img_{uid}",
                        file_name=f"images/{fname}",
                        media_type=media_type_for_ext(ext),
                        content=data,
                    )
                    book.add_item(item)
                    image_items[src] = item
                except Exception:
                    continue

            img["src"] = image_items[src].file_name
            img.attrs.pop("srcset", None)
            img.attrs.pop("sizes", None)

        body_content = soup.body.decode_contents() if soup.body else str(soup)
        pages.append(_add_page(
            book, css, display_title, f"{chapter_slug}.xhtml",
            f"<h1>{post.title}</h1>\n{body_content}",
            spec.lang,
        ))

    # ---- Back matter ----
    for file_attr, page_title, file_name, placeholder in [
        (spec.notes_file, "Notes", "notes.xhtml",
         "[Notes placeholder — endnotes, references, etc.]"),
        (spec.acknowledgements_file, "Acknowledgements", "acknowledgements.xhtml",
         "[Acknowledgements placeholder — thank-yous and credits.]"),
        (spec.about_file, "About This Book", "about.xhtml",
         "[About this publication placeholder — production credits, source, rights, etc.]"),
    ]:
        html = _load_or_placeholder(file_attr, page_title, placeholder)
        pages.append(_add_page(book, css, page_title, file_name, html, spec.lang))

    # ---- Finalize ----
    book.toc = tuple(pages)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    if spec.cover:
        cover_page = next(p for p in pages if p.id == "cover")
        rest = [p for p in pages if p.id != "cover"]
        book.spine = [cover_page, "nav"] + rest
    else:
        book.spine = ["nav"] + pages

    epub.write_epub(spec.output, book)
    return spec.output
