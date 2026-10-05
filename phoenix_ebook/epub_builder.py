"""Assembles an EPUB from a BookSpec and fetched posts: chapters, images, front/back matter, navigation."""
from __future__ import annotations

import hashlib
import html
import io
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image

from phoenix_ebook.images import ImageFetchError, fetch_image, optimize_image, validate_image
from phoenix_ebook.models import Author, BookSpec, BuildProblem, BuildResult, Post
from phoenix_ebook.processors.base import HtmlProcessor


CSS = """
body { font-family: Georgia, serif; line-height: 1.6; margin: 1em; }
img { max-width: 100%; height: auto; display: block; margin: 1em 0; }
h1, h2, h3 { font-family: sans-serif; }
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
    return f"<h2>{title}</h2>\n{paragraphs}"


def _add_page(book, css, title, file_name, content, lang):
    if not content or not content.strip():
        content = f"<p>({title} — content placeholder)</p>"
    page = epub.EpubHtml(title=title, file_name=file_name, lang=lang)
    page.content = content
    page.add_item(css)
    book.add_item(page)
    return page


def _load_or_placeholder(path, title, placeholder_text, use_placeholder: bool) -> str | None:
    """A section's HTML: its file, else a placeholder page, else None (section omitted)."""
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    if use_placeholder:
        return f"<h2>{title}</h2>\n<p>{placeholder_text}</p>"
    return None


class _Writer(epub.EpubWriter):
    """ebooklib's writer, with two fixes to the landmarks nav it generates:

    - EPUB 3 landmark terms where ebooklib reuses EPUB 2 guide types;
    - ``hidden``, so the landmarks list (meant for reading apps) doesn't render on
      the contents page, which is in the reading order.
    """

    def _get_nav(self, item):
        nav = super()._get_nav(item)
        nav = nav.replace(b'epub:type="title-page"', b'epub:type="titlepage"')
        return nav.replace(b'<nav epub:type="landmarks">', b'<nav epub:type="landmarks" hidden="hidden">')


class MissingContentFile(ValueError):
    """A content-file option names a path that doesn't exist."""


# BookSpec field -> how the user spelled it, for error messages.
CONTENT_FILE_OPTIONS = {
    "cover": "--cover / [content] cover",
    "copyright_file": "--copyright-file / [content] copyright_file",
    "imprint_file": "--imprint-file / [content] imprint_file",
    "foreword_file": "--foreword-file / [content] foreword_file",
    "intro_file": "--intro-file / [content] intro_file",
    "notes_file": "--notes-file / [content] notes_file",
    "acknowledgements_file": "--acknowledgements-file / [content] acknowledgements_file",
    "about_file": "--about-file / [content] about_file",
}


def check_content_files(spec: BookSpec) -> None:
    """Raise MissingContentFile if any content-file path in ``spec`` doesn't exist.

    Call before fetching posts, so a typo fails fast. ``build()`` also calls it.
    """
    for field, option in CONTENT_FILE_OPTIONS.items():
        path = getattr(spec, field)
        if path and not Path(path).is_file():
            raise MissingContentFile(f"{option}: file not found: {path}")


def _remove_image(img) -> None:
    """Remove an <img>, plus any <a> or <figure> wrapper it leaves empty."""
    link = img.find_parent("a")
    figure = img.find_parent("figure")
    img.decompose()
    if link is not None and not link.get_text(strip=True) and link.find(True) is None:
        link.decompose()
    if figure is not None and figure.find("img") is None:
        figure.decompose()


FEATURE_MARK = "data-feature-image"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _format_date(timestamp: str | None) -> str | None:
    """'2026-08-24T20:04:00-04:00' -> '24 Aug 2026' (the date as written, no tz shift)."""
    if not timestamp:
        return None
    try:
        d = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{d.day:02d} {_MONTHS[d.month - 1]} {d.year}"


def _byline(authors: list[Author]) -> str | None:
    if not authors:
        return None
    names = [
        f'<a href="{html.escape(a.url)}">{html.escape(a.name)}</a>' if a.url else html.escape(a.name)
        for a in authors
    ]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"By {joined}"


def _article_header(post: Post, feature_figure: str) -> str:
    parts = ["<header>"]
    if date := _format_date(post.published_at):
        parts.append(f'<p class="date">{date}</p>')
    parts.append(f"<h2>{html.escape(post.title)}</h2>")
    if byline := _byline(post.authors):
        parts.append(f'<p class="byline">{byline}</p>')
    if feature_figure:
        parts.append(feature_figure)
    parts.append("</header>")
    return "\n".join(parts)


def _title_page(spec: BookSpec) -> str:
    parts = ['<section epub:type="titlepage">', f"<h1>{html.escape(spec.title)}</h1>"]
    if spec.author:
        parts.append(f'<p class="author">{html.escape(spec.author)}</p>')
    if spec.publisher:
        parts.append(f'<p class="publisher">{html.escape(spec.publisher)}</p>')
    parts.append("</section>")
    return "\n".join(parts)


def _half_title_page(spec: BookSpec) -> str:
    return (f'<section epub:type="halftitlepage">\n'
            f'<p class="title">{html.escape(spec.title)}</p>\n</section>')


def _insert_feature_image(soup, post: Post, image_base_url: str) -> None:
    """Put the post's feature image at the top of its content, as a <figure>.

    Inserted before cleaning and image handling so it gets the same treatment
    as body images (fetch, validation, optimization, failure reporting).
    """
    if not post.feature_image:
        return
    resolve = (lambda u: urljoin(image_base_url, u)) if image_base_url else (lambda u: u)
    url = resolve(post.feature_image)
    if any(resolve(img.get("src", "")) == url for img in soup.find_all("img")):
        return
    figure = soup.new_tag("figure", attrs={FEATURE_MARK: ""})
    figure.append(soup.new_tag("img", src=post.feature_image, alt=post.feature_image_alt or ""))
    if post.feature_image_caption:
        caption = soup.new_tag("figcaption")
        caption.append(BeautifulSoup(post.feature_image_caption, "html.parser"))
        figure.append(caption)
    soup.insert(0, figure)


def build(
    spec: BookSpec,
    posts: Iterable[Post],
    processor: HtmlProcessor,
    *,
    session: requests.Session | None = None,
    image_base_url: str = "",
) -> BuildResult:
    """Assemble an EPUB from already-fetched posts.

    Images that can't be fetched as valid images are left out of the book and
    reported in BuildResult.problems; they never fail the build.
    """
    check_content_files(spec)
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

    front: list[epub.EpubHtml] = []      # reading order before the contents page
    after_toc: list[epub.EpubHtml] = []  # foreword, intro: after the contents page
    chapters: list[epub.EpubHtml] = []
    contents_labels: dict[str, str] = {}  # chapter file name -> processor's contents label
    back: list[epub.EpubHtml] = []
    image_items: dict[str, epub.EpubItem] = {}
    image_sizes: dict[str, tuple[int, int] | None] = {}
    failed_images: dict[str, str] = {}  # src -> reason
    problems: list[BuildProblem] = []

    # ---- Cover (metadata only: no cover page, see #6) ----
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

        cover_item = epub.EpubItem(
            uid="cover-image",
            file_name=f"images/cover{cover_ext}",
            media_type=media_type_for_ext(cover_ext),
            content=cover_bytes,
        )
        cover_item.properties = {"cover-image"}
        book.add_item(cover_item)
        book.add_metadata(None, "meta", "", {"name": "cover", "content": "cover-image"})

    # ---- Front matter ----
    title_page = _add_page(book, css, "Title Page", "titlepage.xhtml", _title_page(spec), spec.lang)
    for file_attr, page_title, file_name, placeholder, bucket in [
        (spec.copyright_file, "Copyright", "copyright.xhtml",
         "[Copyright placeholder — replace with publication copyright notice.]", front),
        (spec.imprint_file, "Imprint", "imprint.xhtml",
         "[Imprint placeholder — publisher, edition, printing history, etc.]", front),
        (spec.foreword_file, "Foreword", "foreword.xhtml",
         "[Foreword placeholder — introductory remarks by a guest writer.]", after_toc),
    ]:
        content = _load_or_placeholder(file_attr, page_title, placeholder, spec.placeholders)
        if content is not None:
            bucket.append(_add_page(book, css, page_title, file_name, content, spec.lang))

    if spec.intro_file:
        intro_text = Path(spec.intro_file).read_text(encoding="utf-8").strip()
        if intro_text:
            after_toc.append(_add_page(
                book, css, "Introduction", "intro.xhtml",
                wrap_text_as_html("Introduction", intro_text),
                spec.lang,
            ))

    # ---- Chapters ----
    for chapter_number, post in enumerate(posts, start=1):
        chapter_slug = sanitize_filename(post.slug)
        soup = BeautifulSoup(post.html, "html.parser")
        _insert_feature_image(soup, post, image_base_url)
        processor.clean(soup, post)
        contents_labels[f"{chapter_slug}.xhtml"] = processor.display_title(post)

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

            if src not in image_items and src not in failed_images:
                try:
                    data, content_type = fetch_image(session, src)
                    try:
                        ext = validate_image(data, ext)
                    except ImageFetchError as exc:
                        raise ImageFetchError(f"{exc} (Content-Type: {content_type})") from None
                    size = None
                    if spec.optimize_images:
                        data, ext, size = optimize_image(
                            data, ext, max_width=spec.image_max_width, quality=spec.image_quality
                        )
                    fname = f"{chapter_slug}_{uid}{ext}"
                    item = epub.EpubItem(
                        uid=f"img_{uid}",
                        file_name=f"images/{fname}",
                        media_type=media_type_for_ext(ext),
                        content=data,
                    )
                    book.add_item(item)
                    image_items[src] = item
                    image_sizes[src] = size
                except ImageFetchError as exc:
                    failed_images[src] = str(exc)

            if src in failed_images:
                problems.append(BuildProblem(
                    kind="image-download-failed", post_slug=post.slug, url=src,
                    detail=failed_images[src],
                ))
                _remove_image(img)
                continue

            img["src"] = image_items[src].file_name
            if image_sizes.get(src) and img.has_attr("width") and img.has_attr("height"):
                img["width"], img["height"] = (str(n) for n in image_sizes[src])
            img.attrs.pop("srcset", None)
            img.attrs.pop("sizes", None)

        feature = soup.find("figure", attrs={FEATURE_MARK: True})
        feature_html = ""
        if feature is not None:  # absent if the feature image failed to download
            del feature[FEATURE_MARK]
            feature_html = str(feature.extract())
        body_content = soup.body.decode_contents() if soup.body else str(soup)
        chapters.append(_add_page(
            book, css, post.title, f"{chapter_slug}.xhtml",
            f'<article id="article-{chapter_number}">\n'
            f"{_article_header(post, feature_html)}\n{body_content}\n</article>",
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
        content = _load_or_placeholder(file_attr, page_title, placeholder, spec.placeholders)
        if content is not None:
            back.append(_add_page(book, css, page_title, file_name, content, spec.lang))

    # ---- Half-title page: divides real front matter from the chapters ----
    half_title = []
    if front or after_toc:
        half_title = [_add_page(book, css, spec.title, "halftitlepage.xhtml", _half_title_page(spec), spec.lang)]

    # ---- Navigation and reading order ----
    # Reading order: title page, copyright, imprint, contents, foreword, intro,
    # half-title, chapters, back matter. The contents page starts at the foreword:
    # it leaves out the pages before it (title page, copyright, imprint), the
    # half-title page and itself.
    chapter_links = [epub.Link(page.file_name, contents_labels[page.file_name], page.id) for page in chapters]
    book.toc = tuple(after_toc + chapter_links + back)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [title_page, *front, "nav", *after_toc, *half_title, *chapters, *back]

    book.guide.append({"type": "title-page", "title": "Title Page", "href": title_page.file_name})
    book.guide.append({"type": "toc", "title": "Contents", "href": "nav.xhtml"})
    if chapters:
        book.guide.append({"type": "text", "title": "Start", "href": chapters[0].file_name})

    writer = _Writer(spec.output, book, {})
    writer.process()
    writer.write()
    return BuildResult(path=spec.output, problems=problems)
