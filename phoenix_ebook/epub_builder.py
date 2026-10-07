"""Assembles an EPUB from a BookSpec and fetched posts: chapters, images, front/back matter, navigation."""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment
from ebooklib import epub
from PIL import Image

from phoenix_ebook.alt_text import normalize_image_url, suspicious_alt
from phoenix_ebook.canonical import (CALL_TO_ACTION, CALL_TO_ACTION_BANNER, CARD_CLASS, DECORATIVE, EMBED_REMOVED,
                                     EMBED_SRC, EMBED_UNKNOWN, FEATURE_IMAGE, LINK_REASON, LINK_REMOVED,
                                     LINK_REPAIRED, LINK_UNLINKED, OEMBED_TEXT, THUMBNAIL_FALLBACK,
                                     THUMBNAIL_LOOKUP)
from phoenix_ebook.dates import format_date as _format_date
from phoenix_ebook.errors import InvalidBookSpec, MissingContentFile, OutputError, PostNotFound
from phoenix_ebook.images import ImageFetchError, fetch_image, optimize_image, validate_image
from phoenix_ebook.models import Author, BookSpec, BuildProblem, BuildResult, Post, SourceSpec
from phoenix_ebook.platforms.base import Platform
from phoenix_ebook.processors.base import HtmlProcessor


# Linked from every page in this order; later files win. core.css is a pinned,
# unmodified Standard Ebooks snapshot (see its header); overrides go in phoenix.css.
STYLES_DIR = Path(__file__).parent / "styles"
STYLESHEETS = ("core.css", "phoenix.css")


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
    """Content-file text as HTML: HTML passes through unchanged; plain text becomes
    an <h2> title plus one paragraph per non-empty line."""
    if text.strip().startswith("<"):
        return text
    paragraphs = "".join(f"<p>{line}</p>" for line in text.splitlines() if line.strip())
    return f"<h2>{title}</h2>\n{paragraphs}"


class _Page(epub.EpubHtml):
    """A content page whose <body> carries an EPUB part role (frontmatter/bodymatter/backmatter).

    ebooklib can't set <body> attributes, so add the role to its generated markup.
    """

    def __init__(self, *args, part: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.part = part

    def get_content(self, default=None):
        content = super().get_content(default)
        if self.part and content:
            content = content.replace(b"<body>", f'<body epub:type="{self.part}">'.encode(), 1)
        return content


# No-break glue: U+FEFF before em dashes and ellipses, so a line never starts with
# one (what Standard Ebooks' tooling does). Not inside code, never in attributes.
_GLUE = "\ufeff"
_NEEDS_GLUE = re.compile("(?<![\ufeff\u2060\u00a0])([\u2014\u2026])")
_NO_GLUE_TAGS = {"pre", "code", "kbd", "samp", "script", "style"}


def glue(text: str) -> str:
    """'word—word…' -> 'word\\ufeff—word\\ufeff…' (idempotent)."""
    return _NEEDS_GLUE.sub(_GLUE + r"\1", text)


def _glue_soup(soup) -> None:
    for text in soup.find_all(string=True):
        if isinstance(text, Comment) or any(p.name in _NO_GLUE_TAGS for p in text.parents):
            continue
        glued = glue(str(text))
        if glued != text:
            text.replace_with(glued)


# epub:type -> matching DPUB-ARIA role, for screen readers (per DAISY Ace's
# epub-type-has-matching-role). Types not listed have no ARIA counterpart.
ARIA_ROLES = {
    "chapter": "doc-chapter",
    "foreword": "doc-foreword",
    "introduction": "doc-introduction",
    "acknowledgments": "doc-acknowledgments",
}


def _section(role: str, content: str) -> str:
    """Wrap content (e.g. a user's content file) in a section with a specific role, unmodified."""
    aria = f' role="{ARIA_ROLES[role]}"' if role in ARIA_ROLES else ""
    return f'<section epub:type="{role}"{aria}>\n{content}\n</section>'


def _add_page(book, styles, title, file_name, content, lang, part=None):
    if not content or not content.strip():
        content = f"<p>({title} — content placeholder)</p>"
    page = _Page(title=title, file_name=file_name, lang=lang, part=part)
    page.content = content
    for style in styles:
        page.add_item(style)
    book.add_item(page)
    return page


def _load_or_placeholder(path, title, placeholder_text, use_placeholder: bool) -> str | None:
    """A section's HTML: its file, else a placeholder page, else None (section omitted)."""
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    if use_placeholder:
        return f"<h2>{title}</h2>\n<p>{glue(placeholder_text)}</p>"
    return None


class _Writer(epub.EpubWriter):
    """ebooklib's writer, with xml:lang on the OPF <package>, and two fixes to the
    landmarks nav it generates:

    - EPUB 3 landmark terms where ebooklib reuses EPUB 2 guide types;
    - ``hidden``, so the landmarks list (meant for reading apps) doesn't render on
      the contents page, which is in the reading order.
    """

    def _write_opf_metadata(self, root):
        root.set("{http://www.w3.org/XML/1998/namespace}lang", self.book.language)  # Ace: epub-lang
        super()._write_opf_metadata(root)

    def _get_nav(self, item):
        nav = super()._get_nav(item)
        nav = nav.replace(b'epub:type="title-page"', b'epub:type="titlepage"')
        nav = nav.replace(b"<body>", b'<body epub:type="frontmatter">', 1)
        return nav.replace(b'<nav epub:type="landmarks">', b'<nav epub:type="landmarks" hidden="hidden">')


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
    "css": "--css / [style] css",
}


def _write_atomically(output: str, book) -> None:
    """Write to a temporary file next to ``output``, then rename it into place.

    An interrupted or failed write never leaves a half-written book, and an
    existing file at ``output`` is only replaced by a complete one.
    """
    out = Path(output)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        # No page list: our books have no print page numbers, and ebooklib would treat
        # every element with both epub:type and id (e.g. each chapter's <section>) as
        # a page break, producing a bogus "page list" that reading apps show.
        writer = _Writer(str(tmp), book, {"epub3_pages": False})
        writer.process()
        writer.write()
        os.replace(tmp, out)
    except OSError as exc:
        raise OutputError(f"can't write {output}: {exc.strerror or exc}") from exc
    finally:
        if tmp.exists():
            tmp.unlink()


def validate_spec(spec: BookSpec) -> None:
    """Raise InvalidBookSpec (or MissingContentFile) if ``spec`` can't be built.

    Call before fetching posts, so mistakes fail fast. ``build()`` also calls it.
    """
    if spec.series_number and not spec.series:
        raise InvalidBookSpec("--series-number / [book] series_number needs --series / [book] series",
                              field="series_number")
    if spec.issn and not spec.series:
        raise InvalidBookSpec("--issn / [book] issn identifies a series, so it needs --series / [book] series",
                              field="issn")
    if spec.isbn and _isbn_identifier(spec.isbn)[1] is None:
        raise InvalidBookSpec(f"--isbn / [book] isbn: {spec.isbn!r} is not a valid ISBN-10 or ISBN-13", field="isbn")
    if spec.issn and normalize_issn(spec.issn) is None:
        raise InvalidBookSpec(f"--issn / [book] issn: {spec.issn!r} is not a valid ISSN", field="issn")
    check_content_files(spec)
    _check_output_path(spec.output)


def _check_output_path(output: str) -> None:
    folder = Path(output).resolve().parent
    if not folder.is_dir():
        raise OutputError(f"can't write {output}: the folder {folder} doesn't exist")
    if not os.access(folder, os.W_OK):
        raise OutputError(f"can't write {output}: no permission to write in {folder}")
    if Path(output).is_dir():
        raise OutputError(f"can't write {output}: it's a folder; give a file name ending in .epub")


def fetch_posts(source: SourceSpec, platform: Platform, secret: str, session: requests.Session) -> list[Post]:
    """Fetch every slug in ``source`` from ``platform``, in order.

    Tries every slug so a batch of typos is reported together: raises one
    ``PostNotFound`` listing every missing slug once all have been tried.
    Other errors (auth, network) stop immediately. Shared by the CLI and the
    website so both report missing posts the same way.
    """
    posts, missing, site = [], [], source.url
    for slug in source.slugs:
        try:
            posts.append(platform.fetch_post(session, source.url, secret, slug))
        except PostNotFound as exc:
            missing.extend(exc.slugs)
            site = exc.site
    if missing:
        raise PostNotFound(site, missing)
    return posts


def check_content_files(spec: BookSpec) -> None:
    """Raise MissingContentFile if any content-file path in ``spec`` doesn't exist.

    Call before fetching posts, so a typo fails fast. ``build()`` also calls it.
    """
    for field, option in CONTENT_FILE_OPTIONS.items():
        path = getattr(spec, field)
        if path and not Path(path).is_file():
            raise MissingContentFile(f"{option}: file not found: {path}", field=field)
        if path and field not in ("cover",):
            try:
                Path(path).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise InvalidBookSpec(f"{option}: {path} isn't UTF-8 text; save it as UTF-8 "
                                      "(most editors offer this under 'Save As' or 'Encoding')",
                                      field=field) from None


def _remove_image(img) -> None:
    """Remove an <img>, plus any <a> or <figure> wrapper it leaves empty."""
    link = img.find_parent("a")
    figure = img.find_parent("figure")
    img.decompose()
    if link is not None and not link.get_text(strip=True) and link.find(True) is None:
        link.decompose()
    if figure is not None and figure.find("img") is None and CARD_CLASS not in figure.get("class", []):
        figure.decompose()  # a link card stays: its text and link still work without the picture


FEATURE_MARK = FEATURE_IMAGE
def _byline(authors: list[Author]) -> str | None:
    if not authors:
        return None
    names = [
        f'<a href="{html.escape(a.url)}">{html.escape(a.name)}</a>' if a.url else html.escape(a.name)
        for a in authors
    ]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"By {joined}"


def _chapter_header(post: Post, feature_figure: str) -> str:
    parts = ["<header>"]
    if date := _format_date(post.published_at):
        parts.append(f'<p class="date">{date}</p>')
    parts.append(f"<h2>{glue(html.escape(post.title))}</h2>")
    if byline := _byline(post.authors):
        parts.append(f'<p class="byline">{byline}</p>')
    if feature_figure:
        parts.append(feature_figure)
    parts.append("</header>")
    return "\n".join(parts)


def sort_name(name: str, overrides: dict[str, str]) -> str:
    """'J.D. Connor' -> 'Connor, J.D.' (last word first), unless overridden."""
    if name in overrides:
        return overrides[name]
    words = name.split()
    if len(words) < 2 or "," in name:
        return name
    return f"{words[-1]}, {' '.join(words[:-1])}"


def _isbn_check_ok(digits: str) -> bool:
    if len(digits) == 13 and digits.isdigit():
        return sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits)) % 10 == 0
    if len(digits) == 10 and digits[:9].isdigit() and (digits[9].isdigit() or digits[9] == "X"):
        values = [int(d) for d in digits[:9]] + [10 if digits[9] == "X" else int(digits[9])]
        return sum(v * (10 - i) for i, v in enumerate(values)) % 11 == 0
    return False


def _isbn_identifier(isbn: str) -> tuple[str, str | None]:
    """'979-8-99-202554-5' -> ('urn:isbn:9798992025545', '15'): the URN and its ONIX
    codelist 5 type ('15' ISBN-13, '02' ISBN-10), or None as the type if invalid."""
    digits = "".join(ch for ch in isbn if ch.isalnum()).upper()
    valid = _isbn_check_ok(digits)
    return f"urn:isbn:{digits}", {13: "15", 10: "02"}.get(len(digits)) if valid else None


def normalize_issn(issn: str) -> str | None:
    """'03178471' / '0317-8471' -> '0317-8471'; None if it isn't a valid ISSN (check digit may be X)."""
    chars = "".join(ch for ch in issn if ch.isalnum()).upper()
    if len(chars) != 8 or not chars[:7].isdigit() or not (chars[7].isdigit() or chars[7] == "X"):
        return None
    check = (11 - sum(int(d) * (8 - i) for i, d in enumerate(chars[:7])) % 11) % 11
    if ("X" if check == 10 else str(check)) != chars[7]:
        return None
    return f"{chars[:4]}-{chars[4:]}"


def _refine(book, target_id: str, prop: str, value: str, scheme: str | None = None) -> None:
    attrs = {"refines": f"#{target_id}", "property": prop}
    if scheme:
        attrs["scheme"] = scheme
    book.add_metadata(None, "meta", value, attrs)


def _add_metadata(book, spec: BookSpec, posts: list[Post]) -> None:
    """Package metadata: identifier, titles, credits, series and the rest."""
    if spec.isbn:
        urn, isbn_type = _isbn_identifier(spec.isbn)
        book.set_identifier(urn)
        if isbn_type:
            _refine(book, book.IDENTIFIER_ID, "identifier-type", isbn_type, "onix:codelist5")
    else:
        book.set_identifier(f"urn:uuid:{uuid.uuid4()}")

    book.title = spec.title
    book.add_metadata("DC", "title", spec.title, {"id": "title"})
    if spec.subtitle:
        _refine(book, "title", "title-type", "main")
        book.add_metadata("DC", "title", spec.subtitle, {"id": "subtitle"})
        _refine(book, "subtitle", "title-type", "subtitle")
        book.add_metadata("DC", "title", f"{spec.title}: {spec.subtitle}", {"id": "fulltitle"})
        _refine(book, "fulltitle", "title-type", "expanded")
    book.set_language(spec.lang)

    # Credits: the editor is the creator and post authors are contributors, so
    # apps that show only the first creator don't shelve an anthology under one
    # writer. Without an editor, the post authors are the creators.
    authors: list[str] = []
    for post in posts:
        for author in post.authors:
            if author.name not in authors:
                authors.append(author.name)
    if spec.editor:
        book.add_metadata("DC", "creator", spec.editor, {"id": "editor"})
        _refine(book, "editor", "role", "edt", "marc:relators")
        if spec.editor in spec.sort_names:
            _refine(book, "editor", "file-as", spec.sort_names[spec.editor])
    element, prefix = ("contributor", "contributor") if spec.editor else ("creator", "author")
    for n, name in enumerate(authors, start=1):
        book.add_metadata("DC", element, name, {"id": f"{prefix}-{n}"})
        _refine(book, f"{prefix}-{n}", "role", "aut", "marc:relators")
        _refine(book, f"{prefix}-{n}", "file-as", sort_name(name, spec.sort_names))

    if spec.series:
        book.add_metadata(None, "meta", spec.series, {"property": "belongs-to-collection", "id": "series"})
        _refine(book, "series", "collection-type", "series")
        if spec.series_number:
            _refine(book, "series", "group-position", str(spec.series_number))
        if spec.issn:
            _refine(book, "series", "dcterms:identifier", f"urn:issn:{normalize_issn(spec.issn)}")

    if spec.publisher:
        book.add_metadata("DC", "publisher", spec.publisher)
    if spec.description:
        book.add_metadata("DC", "description", spec.description)
    if spec.pub_date:
        book.add_metadata("DC", "date", spec.pub_date)
    if spec.rights:
        book.add_metadata("DC", "rights", spec.rights)


def _title_page(spec: BookSpec) -> str:
    parts = ['<section epub:type="titlepage">', f"<h1>{glue(html.escape(spec.title))}</h1>"]
    if spec.subtitle:
        parts.append(f'<p class="subtitle">{glue(html.escape(spec.subtitle))}</p>')
    if spec.series:
        series = spec.series + (f" · No. {spec.series_number}" if spec.series_number else "")
        series += f" · ISSN {normalize_issn(spec.issn)}" if spec.issn else ""
        parts.append(f'<p class="series">{html.escape(series)}</p>')
    if spec.editor:
        parts.append(f'<p class="editor">{html.escape(spec.editor)}</p>')
    if spec.publisher:
        parts.append(f'<p class="publisher">{html.escape(spec.publisher)}</p>')
    parts.append("</section>")
    return "\n".join(parts)


def _half_title_page(spec: BookSpec) -> str:
    subtitle = f'<p class="subtitle">{glue(html.escape(spec.subtitle))}</p>\n' if spec.subtitle else ""
    return (f'<section epub:type="halftitlepage">\n'
            f'<p class="title">{glue(html.escape(spec.title))}</p>\n{subtitle}</section>')


def _report_calls_to_action(soup, post: Post, problems: list[BuildProblem]) -> None:
    """Report and unmark what platforms and processors marked as calls-to-action (#20).

    One problem per paragraph whose call-to-action link was unwrapped (the words
    stay), and one per promotional image kept because it isn't at the end.
    """
    reported = set()
    for span in soup.find_all("span", attrs={CALL_TO_ACTION: True}):
        block = span.find_parent(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "blockquote"]) or span
        if id(block) not in reported:
            reported.add(id(block))
            text = " ".join(block.get_text().split())
            image = span.find("img")
            if not text and image is not None:  # a linked image with no words: describe the image
                figure = span.find_parent("figure")
                caption = figure.find("figcaption") if figure else None
                label = (caption and " ".join(caption.get_text().split())) or image.get("alt") or image.get("src", "")
                detail = f"link removed from an image (image kept): \"{label[:200]}\""
            else:
                detail = f"link removed, words kept: \"{text[:200]}\""
            problems.append(BuildProblem(kind="call-to-action", post_slug=post.slug, url=span[CALL_TO_ACTION],
                                         detail=detail))
        span.unwrap()
    for figure in soup.find_all(attrs={CALL_TO_ACTION_BANNER: True}):
        link = figure.find("a", href=True)
        caption = figure.find("figcaption")
        text = " ".join(caption.get_text().split()) if caption else ""
        problems.append(BuildProblem(kind="call-to-action", post_slug=post.slug, url=link["href"] if link else None,
                                     detail="promotional image kept (not at the end of the post)"
                                            + (f": \"{text[:200]}\"" if text else "")))
        del figure[CALL_TO_ACTION_BANNER]


def _report_link_changes(soup, post: Post, problems: list[BuildProblem]) -> None:
    """Report and unmark every link the cleanup repaired, unlinked or removed (#22)."""
    def words(tag) -> str:
        return " ".join(tag.get_text().split())[:120]

    for link in soup.find_all("a", attrs={LINK_REPAIRED: True}):
        problems.append(BuildProblem(kind="link-repaired", post_slug=post.slug, url=link[LINK_REPAIRED],
                                     detail=f"\"{words(link)}\": now {link['href']} "
                                            f"({link.get(LINK_REASON, 'repaired')})"))
        del link[LINK_REPAIRED]
        link.attrs.pop(LINK_REASON, None)
    for span in soup.find_all("span", attrs={LINK_UNLINKED: True}):
        problems.append(BuildProblem(kind="link-unlinked", post_slug=post.slug, url=span[LINK_UNLINKED],
                                     detail=f"\"{words(span)}\": link removed, words kept "
                                            f"({span.get(LINK_REASON, 'broken')})"))
        span.unwrap()
    for span in soup.find_all("span", attrs={LINK_REMOVED: True}):
        problems.append(BuildProblem(kind="link-empty-removed", post_slug=post.slug, url=span[LINK_REMOVED],
                                     detail="a link with no text was removed"))
        span.unwrap()


def _report_embeds(soup, post: Post, problems: list[BuildProblem]) -> None:
    """Report and unmark embeds left out of the book or turned into generic link cards (#21)."""
    for marker in soup.find_all(attrs={EMBED_REMOVED: True}):
        problems.append(BuildProblem(kind="embed-removed", post_slug=post.slug, url=marker.get(EMBED_SRC),
                                     detail=f"{marker[EMBED_REMOVED]} left out (it only works on the website)"))
        marker.decompose()
    for card in soup.find_all(attrs={EMBED_UNKNOWN: True}):
        problems.append(BuildProblem(kind="embed-unknown", post_slug=post.slug, url=card[EMBED_UNKNOWN],
                                     detail="unrecognised embedded content became a link card; check it reads well"))
        del card[EMBED_UNKNOWN]


def _lookup_thumbnails(soup, post: Post, session, cache: dict, problems: list[BuildProblem]) -> None:
    """Ask oEmbed endpoints for link-card thumbnails (and author names) before images are fetched.

    A failed lookup leaves the card without a thumbnail, reported like a failed image.
    """
    for img in soup.find_all("img", attrs={THUMBNAIL_LOOKUP: True}):
        endpoint = img.attrs.pop(THUMBNAIL_LOOKUP)
        if endpoint not in cache:
            try:
                body, _ = fetch_image(session, endpoint)
                cache[endpoint] = json.loads(body)
                if not isinstance(cache[endpoint], dict):
                    raise ValueError("not a JSON object")
            except (ImageFetchError, ValueError) as exc:
                cache[endpoint] = f"thumbnail lookup failed: {exc}"
        answer = cache[endpoint]
        card = img.find_parent("figure") or soup
        if isinstance(answer, dict):
            for element in card.find_all(attrs={OEMBED_TEXT: True}):
                try:
                    element.string = element[OEMBED_TEXT].format_map(answer)
                except (KeyError, ValueError, IndexError):
                    pass  # keep the text the platform wrote
        thumbnail = answer.get("thumbnail_url") if isinstance(answer, dict) else None
        if isinstance(thumbnail, str) and thumbnail.startswith(("http://", "https://")):
            img["src"] = thumbnail
        else:
            problems.append(BuildProblem(kind="image-download-failed", post_slug=post.slug, url=endpoint,
                                         detail=answer if isinstance(answer, str) else "no thumbnail in the answer"))
            img.decompose()
    for element in soup.find_all(attrs={OEMBED_TEXT: True}):
        del element[OEMBED_TEXT]


def _is_animated(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return bool(getattr(img, "is_animated", False))
    except Exception:
        return False


def _add_accessibility_metadata(book, spec: BookSpec, tally: Counter, has_animation: bool) -> None:
    """schema.org accessibility metadata, computed from what the book actually contains.

    Makes no conformance claim: every statement is something the build verified.
    Reading apps such as Thorium show these in their book-info dialog.
    """
    images, missing = tally["images"], tally["missing"]
    all_images_have_alt = images > 0 and missing == 0

    def schema(prop: str, value: str) -> None:
        book.add_metadata(None, "meta", value, {"property": f"schema:{prop}"})

    schema("accessMode", "textual")
    if images:
        schema("accessMode", "visual")
    if not images or all_images_have_alt:
        schema("accessModeSufficient", "textual")
    if images:
        schema("accessModeSufficient", "textual,visual")

    features = ["tableOfContents", "readingOrder", "structuralNavigation", "displayTransformability"]
    if all_images_have_alt:
        features.append("alternativeText")
    for feature in features:
        schema("accessibilityFeature", feature)

    if has_animation:
        for hazard in ("unknownFlashingHazard", "noMotionSimulationHazard", "noSoundHazard"):
            schema("accessibilityHazard", hazard)
    else:
        schema("accessibilityHazard", "none")

    schema("accessibilitySummary", spec.accessibility_summary or _accessibility_summary(tally))


def _accessibility_summary(tally: Counter) -> str:
    parts = ["Includes a table of contents and structural navigation.",
             "Text can be resized and the reader's own font is used."]
    images = tally["images"]
    if images:
        described, decorative = tally["described"], tally["decorative"]
        line = f"{described} of {images} images have text descriptions"
        if decorative:
            line += f"; {decorative} {'is' if decorative == 1 else 'are'} decorative"
        parts.append(line + ".")
    return " ".join(parts)


def _check_alt_text(soup, post: Post, local_to_url: dict[str, str], overrides: dict[str, str],
                    used: set[str], problems: list[BuildProblem], tally: Counter, canonical_url) -> None:
    """Apply alt-text overrides and report missing or suspicious alt text.

    Runs after image handling, so failed images are gone and ``src`` is the
    embedded path; ``local_to_url`` maps it back to the original URL.
    """
    images = soup.find_all("img")
    for position, img in enumerate(images, start=1):
        url = local_to_url.get(img.get("src", ""), img.get("src", ""))
        key = canonical_url(url)
        figure = img.find_parent("figure")
        caption_tag = figure.find("figcaption") if figure else None
        caption = caption_tag.get_text(" ", strip=True) if caption_tag else None
        where = dict(post_slug=post.slug, url=url, location=f"image {position} of {len(images)}", caption=caption)

        tally["images"] += 1
        decorative = img.attrs.pop(DECORATIVE, None) is not None
        if key in overrides:
            img["alt"] = overrides[key]  # "" marks a decorative image
            used.add(key)
            tally["described" if overrides[key].strip() else "decorative"] += 1
            continue
        if decorative:
            img["alt"] = ""
            tally["decorative"] += 1
            continue
        alt = img.get("alt")
        if alt is None or not alt.strip():
            img["alt"] = ""
            tally["missing"] += 1
            problems.append(BuildProblem(kind="image-missing-alt", detail="no alt text", **where))
            continue
        tally["described"] += 1
        if reason := suspicious_alt(alt, caption):
            problems.append(BuildProblem(kind="image-suspicious-alt", detail=reason, **where))


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
    platform: "Platform | None" = None,
) -> BuildResult:
    """Assemble an EPUB from already-fetched posts.

    Images that can't be fetched as valid images are left out of the book and
    reported in BuildResult.problems; they never fail the build. ``platform``
    supplies platform-specific image-URL rules (e.g. Ghost's size variants) for
    matching alt-text overrides; without it the generic rule is used.
    """
    validate_spec(spec)
    posts = list(posts)
    session = session or requests.Session()

    book = epub.EpubBook()
    _add_metadata(book, spec, posts)

    styles = [
        epub.EpubItem(uid=f"style-{Path(name).stem}", file_name=f"style/{name}", media_type="text/css",
                      content=(STYLES_DIR / name).read_bytes())
        for name in STYLESHEETS
    ]
    if spec.css:
        styles.append(epub.EpubItem(uid="style-user", file_name="style/user.css", media_type="text/css",
                                    content=Path(spec.css).read_bytes()))
    for style in styles:
        book.add_item(style)

    front: list[epub.EpubHtml] = []      # reading order before the contents page
    after_toc: list[epub.EpubHtml] = []  # foreword, intro: after the contents page
    chapters: list[epub.EpubHtml] = []
    contents_labels: dict[str, str] = {}  # chapter file name -> processor's contents label
    back: list[epub.EpubHtml] = []
    image_items: dict[str, epub.EpubItem] = {}
    image_sizes: dict[str, tuple[int, int] | None] = {}
    failed_images: dict[str, str] = {}  # src -> reason
    local_to_url: dict[str, str] = {}  # embedded image path -> original URL
    canonical_url = platform.canonical_image_url if platform is not None else normalize_image_url
    alt_overrides = {canonical_url(url): alt for url, alt in spec.alt_text.items()}
    used_overrides: set[str] = set()
    image_tally: Counter = Counter()  # images / described / decorative / missing, for accessibility metadata
    has_animation = False
    oembed_answers: dict = {}  # oEmbed URL -> answer (dict) or failure reason, once per book
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
    title_page = _add_page(book, styles, "Title Page", "titlepage.xhtml", _title_page(spec), spec.lang,
                           part="frontmatter")
    for file_attr, page_title, file_name, placeholder, bucket, role in [
        (spec.copyright_file, "Copyright", "copyright.xhtml",
         "[Copyright placeholder — replace with publication copyright notice.]", front, "copyright-page"),
        (spec.imprint_file, "Imprint", "imprint.xhtml",
         "[Imprint placeholder — publisher, edition, printing history, etc.]", front, "imprint"),
        (spec.foreword_file, "Foreword", "foreword.xhtml",
         "[Foreword placeholder — introductory remarks by a guest writer.]", after_toc, "foreword"),
    ]:
        content = _load_or_placeholder(file_attr, page_title, placeholder, spec.placeholders)
        if content is not None:
            bucket.append(_add_page(book, styles, page_title, file_name, _section(role, content), spec.lang,
                                    part="frontmatter"))

    if spec.intro_file:
        intro_text = Path(spec.intro_file).read_text(encoding="utf-8").strip()
        if intro_text:
            after_toc.append(_add_page(
                book, styles, "Introduction", "intro.xhtml",
                _section("introduction", wrap_text_as_html("Introduction", intro_text)),
                spec.lang, part="frontmatter",
            ))

    # ---- Chapters ----
    for chapter_number, post in enumerate(posts, start=1):
        chapter_slug = sanitize_filename(post.slug)
        soup = BeautifulSoup(post.html, "html.parser")
        _insert_feature_image(soup, post, image_base_url)
        processor.clean(soup, post)
        _report_calls_to_action(soup, post, problems)
        contents_labels[f"{chapter_slug}.xhtml"] = processor.display_title(post)

        _report_embeds(soup, post, problems)
        _report_link_changes(soup, post, problems)
        _lookup_thumbnails(soup, post, session, oembed_answers, problems)

        def load(src: str) -> None:
            """Fetch, check and embed ``src`` once; failures go to ``failed_images``."""
            nonlocal has_animation
            if src in image_items or src in failed_images:
                return
            ext = os.path.splitext(urlparse(src).path)[1].lower() or ".jpg"
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
                ext = ".jpg"
            uid = hashlib.sha1(src.encode()).hexdigest()[:12]
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
                has_animation = has_animation or _is_animated(data)
                item = epub.EpubItem(
                    uid=f"img_{uid}",
                    file_name=f"images/{chapter_slug}_{uid}{ext}",
                    media_type=media_type_for_ext(ext),
                    content=data,
                )
                book.add_item(item)
                image_items[src] = item
                image_sizes[src] = size
            except ImageFetchError as exc:
                failed_images[src] = str(exc)

        resolve = (lambda u: urljoin(image_base_url, u)) if image_base_url else (lambda u: u)
        for img in soup.find_all("img"):
            fallback = img.attrs.pop(THUMBNAIL_FALLBACK, None)
            if not img.get("src"):
                continue
            src = resolve(img["src"])
            load(src)
            if src in failed_images and fallback:
                load(resolve(fallback))
                if resolve(fallback) in image_items:
                    src = resolve(fallback)

            if src in failed_images:
                problems.append(BuildProblem(
                    kind="image-download-failed", post_slug=post.slug, url=src,
                    detail=failed_images[src],
                ))
                _remove_image(img)
                continue

            img["src"] = image_items[src].file_name
            local_to_url[img["src"]] = src
            if image_sizes.get(src) and img.has_attr("width") and img.has_attr("height"):
                img["width"], img["height"] = (str(n) for n in image_sizes[src])
            img.attrs.pop("srcset", None)
            img.attrs.pop("sizes", None)

        _check_alt_text(soup, post, local_to_url, alt_overrides, used_overrides, problems, image_tally, canonical_url)
        _glue_soup(soup)

        feature = soup.find("figure", attrs={FEATURE_MARK: True})
        feature_html = ""
        if feature is not None:  # absent if the feature image failed to download
            del feature[FEATURE_MARK]
            feature_html = str(feature.extract())
        body_content = soup.body.decode_contents() if soup.body else str(soup)
        chapters.append(_add_page(
            book, styles, post.title, f"{chapter_slug}.xhtml",
            # <section>, not <article>: HTML only allows role="doc-chapter" on <section> (epubcheck),
            # and Ace wants that role wherever epub:type="chapter" appears.
            f'<section id="chapter-{chapter_number}" epub:type="chapter" role="doc-chapter">\n'
            f"{_chapter_header(post, feature_html)}\n{body_content}\n</section>",
            spec.lang, part="bodymatter",
        ))

    # ---- Back matter ----
    for file_attr, page_title, file_name, placeholder, role in [
        (spec.notes_file, "Notes", "notes.xhtml",
         "[Notes placeholder — endnotes, references, etc.]", None),
        (spec.acknowledgements_file, "Acknowledgements", "acknowledgements.xhtml",
         "[Acknowledgements placeholder — thank-yous and credits.]", "acknowledgments"),
        (spec.about_file, "About This Book", "about.xhtml",
         "[About this publication placeholder — production credits, source, rights, etc.]", None),
    ]:
        content = _load_or_placeholder(file_attr, page_title, placeholder, spec.placeholders)
        if content is not None:
            if role:
                content = _section(role, content)
            back.append(_add_page(book, styles, page_title, file_name, content, spec.lang, part="backmatter"))

    for url, _ in spec.alt_text.items():
        if canonical_url(url) not in used_overrides:
            problems.append(BuildProblem(kind="alt-override-unused", post_slug=None, url=url,
                                         detail="no image in the book has this URL"))

    # ---- Half-title page: divides real front matter from the chapters ----
    half_title = []
    if front or after_toc:
        half_title = [_add_page(book, styles, spec.title, "halftitlepage.xhtml", _half_title_page(spec), spec.lang,
                                part="frontmatter")]

    # ---- Navigation and reading order ----
    # Reading order: title page, copyright, imprint, contents, foreword, intro,
    # half-title, chapters, back matter. The contents page starts at the foreword:
    # it leaves out the pages before it (title page, copyright, imprint), the
    # half-title page and itself.
    chapter_links = [epub.Link(page.file_name, contents_labels[page.file_name], page.id) for page in chapters]
    book.toc = tuple(after_toc + chapter_links + back)
    book.add_item(epub.EpubNcx())
    nav = epub.EpubNav(title="Contents")
    for style in styles:
        nav.add_item(style)
    book.add_item(nav)
    book.spine = [title_page, *front, "nav", *after_toc, *half_title, *chapters, *back]

    book.guide.append({"type": "title-page", "title": "Title Page", "href": title_page.file_name})
    book.guide.append({"type": "toc", "title": "Contents", "href": "nav.xhtml"})
    if chapters:
        book.guide.append({"type": "text", "title": "Start", "href": chapters[0].file_name})

    _add_accessibility_metadata(book, spec, image_tally, has_animation)

    _write_atomically(spec.output, book)
    return BuildResult(path=spec.output, problems=problems)
