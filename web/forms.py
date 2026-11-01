"""Turns a submitted book form into a ``BookSpec`` and runs the pre-build checks.

Kept free of FastAPI imports so it's testable as plain Python; ``main.py`` adapts
FastAPI's request objects to the plain values this module works with.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Callable, Mapping

import requests

from phoenix_ebook.content import FORMATS, check_content_file, load_content
from phoenix_ebook.epub_builder import fetch_posts, validate_spec
from phoenix_ebook.errors import PhoenixError
from phoenix_ebook.images import DEFAULT_MAX_WIDTH, DEFAULT_QUALITY, ImageFetchError, validate_image
from phoenix_ebook.models import BookSpec, Post, Progress, SourceSpec
from phoenix_ebook.platforms.base import get_platform
from phoenix_ebook.processors.base import select_processor
from phoenix_ebook.secrets import SecretStore, extract_domain

# BookSpec field -> what the form calls it. The cover first, then the content pages in reading order.
UPLOAD_FIELDS = {
    "cover": "Cover image",
    "copyright_file": "Copyright",
    "imprint_file": "Imprint",
    "foreword_file": "Foreword",
    "intro_file": "Introduction",
    "notes_file": "Notes",
    "acknowledgements_file": "Acknowledgements",
    "about_file": "About This Book",
}
CONTENT_FIELDS = tuple(UPLOAD_FIELDS)[1:]



@dataclass(frozen=True)
class Blog:
    """A blog the website builds from. A post's full address says which blog it's on."""

    name: str
    platform: str
    url: str  # where its platform's API is
    hosts: tuple[str, ...]  # exact hosts its post addresses may use; the public one first


BLOGS = (
    Blog("Flaming Hydra", "ghost", "https://flaminghydra.ghost.io",
         ("flaminghydra.com", "www.flaminghydra.com", "flaminghydra.ghost.io")),
    Blog("Welcome to Hell World", "ghost", "https://hellworld.ghost.io",
         ("www.welcometohellworld.com", "welcometohellworld.com", "hellworld.ghost.io")),
)

MAX_POSTS = 100  # see ADR 0002: sized from measured build time against Vercel's function limit
MAX_COVER_BYTES = 4_500_000  # Vercel functions can't return more than 4.5 MB (ADR 0002)


@dataclass
class FormError:
    """One problem with the submission. ``field`` ties it to a form input (for
    ``aria-describedby``); None for a problem that isn't about one field."""

    field: str | None
    message: str


@dataclass
class RawForm:
    """The submitted field values, as strings, plus the uploaded files.

    Mirrors ``BookSpec`` field-for-field (the "simple" fields plus everything
    under "advanced"); see ``build_epub.py``'s ``_spec_from_args`` for the CLI's
    equivalent mapping. Every field is the raw text a browser would submit —
    blank means "not given".
    """

    posts: str = ""
    title: str = ""
    subtitle: str = ""
    editor: str = ""
    # Uploads, by BookSpec field (see UPLOAD_FIELDS): (bytes, file name) as uploaded; files kept from the
    # last build (storage references, used when there's no new upload); where runs.run wrote them to disk.
    files: dict[str, tuple[bytes, str]] = dc_field(default_factory=dict)
    kept: dict[str, str] = dc_field(default_factory=dict)
    paths: dict[str, str] = dc_field(default_factory=dict)

    series: str = ""
    series_number: str = ""
    publisher: str = ""
    description: str = ""
    pub_date: str = ""
    lang: str = "en"
    isbn: str = ""
    issn: str = ""
    rights: str = ""
    image_max_width: str = str(DEFAULT_MAX_WIDTH)
    image_quality: str = str(DEFAULT_QUALITY)
    keep_original_images: bool = False
    placeholders: bool = True
    sort_names: str = ""  # "Name = Last, First" per line
    alt_text: str = ""  # "image URL = alt text" per line


@dataclass
class CheckResult:
    spec: BookSpec | None
    errors: list[FormError] = dc_field(default_factory=list)
    posts: list[Post] = dc_field(default_factory=list)  # fetched while checking, in reading order

    @property
    def ok(self) -> bool:
        return not self.errors

    def errors_for(self, field: str) -> list[str]:
        return [e.message for e in self.errors if e.field == field]


def parse_post_addresses(text: str) -> list[tuple[int, str]]:
    """Non-blank, stripped lines from the textarea, with their 1-based line numbers."""
    return [(i, line) for i, raw in enumerate(text.splitlines(), start=1) if (line := raw.strip())]


def _parse_kv_lines(text: str) -> dict[str, str]:
    """"key = value" per line (blank lines ignored) -> a dict, last one wins.

    The first "=" with a space before it splits the line, so an image address
    containing "=" (``a.png?v=2 = A view``) stays whole.
    """
    result: dict[str, str] = {}
    for _, line in parse_post_addresses(text):
        match = re.match(r"(.*?)\s+=\s*(.*)$", line)
        key, value = (match[1], match[2]) if match else line.partition("=")[::2]
        if key.strip():
            result[key.strip()] = value.strip()
    return result


ALT_KINDS = ("image-missing-alt", "image-suspicious-alt")
MAX_OPEN_ALT_BOXES = 20  # with more in a book, the results page collapses each post's boxes


@dataclass
class AltFix:
    """One alt-text box from the results page."""

    url: str
    text: str = ""
    decorative: bool = False
    current: str = ""  # the alt text the box was prefilled with


def alt_fixes_from(form: Mapping[str, str]) -> list[AltFix]:
    """The results page's alt-text boxes (``fix_url_N``, ``fix_alt_N``, ``fix_decorative_N``, ``fix_current_N``)."""
    fixes = []
    for key, url in form.items():
        if match := re.fullmatch(r"fix_url_(\d+)", key):
            n = match[1]
            fixes.append(AltFix(url, form.get(f"fix_alt_{n}", ""), f"fix_decorative_{n}" in form,
                                form.get(f"fix_current_{n}", "")))
    return fixes


def merge_alt_fixes(alt_text: str, fixes: list[AltFix]) -> str:
    """The Alt text field with the fixes added: decorative as "", new text as written.

    A box left as it was prefilled isn't a fix. Later lines win, so a fix
    replaces an earlier entry for the same image.
    """
    lines = [alt_text.strip()] if alt_text.strip() else []
    for fix in fixes:
        text = " ".join(fix.text.split())
        if fix.decorative:
            lines.append(f"{fix.url} = ")
        elif text and text != " ".join(fix.current.split()):
            lines.append(f"{fix.url} = {text}")
    return "\n".join(lines)


def _blog_of(address: str) -> tuple[Blog | None, str | None, bool]:
    """The blog and slug ``address`` names, and whether it's a full post address at all."""
    for platform in dict.fromkeys(blog.platform for blog in BLOGS):
        if parsed := get_platform(platform).parse_address(address):
            host, slug = parsed
            for blog in BLOGS:
                if blog.platform == platform and host in blog.hosts:
                    return blog, slug, True
            return None, None, True
    return None, None, False


def _line_numbers(numbers: list[int]) -> str:
    return ("line " if len(numbers) == 1 else "lines ") + ", ".join(map(str, numbers))


def _check_post_list(raw_text: str) -> tuple[Blog | None, list[str], list[FormError]]:
    """Resolve full addresses to their blog and slugs, in order; report what isn't a full address,
    unknown sites, addresses from more than one blog, duplicates (with line numbers) and more
    than ``MAX_POSTS`` posts. The blog is None unless exactly one was found."""
    lines = parse_post_addresses(raw_text)
    if not lines:
        return None, [], [FormError("posts", "add at least one post address")]

    errors: list[FormError] = []
    seen: dict[str, int] = {}
    slugs: list[str] = []
    not_addresses: list[int] = []
    other_sites: list[int] = []
    duplicates: list[str] = []
    lines_by_blog: dict[Blog, list[int]] = {}

    for lineno, address in lines:
        blog, slug, is_address = _blog_of(address)
        if blog is None:
            (other_sites if is_address else not_addresses).append(lineno)
            continue
        lines_by_blog.setdefault(blog, []).append(lineno)
        if slug in seen:
            duplicates.append(f"line {lineno} repeats line {seen[slug]} ({slug!r})")
            continue
        seen[slug] = lineno
        slugs.append(slug)

    if not_addresses:
        errors.append(FormError("posts", f"not a full post address: {_line_numbers(not_addresses)}. Paste the "
                                         f"whole address, like https://{BLOGS[0].hosts[0]}/a-post/"))
    if other_sites:
        names = " and ".join(blog.name for blog in BLOGS)
        errors.append(FormError("posts", f"from another site: {_line_numbers(other_sites)}. This website "
                                         f"only builds from {names}"))
    if len(lines_by_blog) > 1:
        where = "; ".join(f"{blog.name}: {_line_numbers(numbers)}" for blog, numbers in lines_by_blog.items())
        errors.append(FormError("posts", f"a book comes from one blog, but these addresses are from more than "
                                         f"one ({where})"))
    if duplicates:
        errors.append(FormError("posts", f"duplicate posts: {'; '.join(duplicates)}"))
    if len(slugs) > MAX_POSTS:
        errors.append(FormError("posts", f"{len(slugs)} posts given; this website builds at most {MAX_POSTS} "
                                         "at a time"))
    return next(iter(lines_by_blog)) if len(lines_by_blog) == 1 else None, slugs, errors


def _check_cover_bytes(data: bytes) -> list[FormError]:
    errors = []
    if len(data) > MAX_COVER_BYTES:
        mb = MAX_COVER_BYTES / 1_000_000
        errors.append(FormError("cover", f"cover image is {len(data) / 1_000_000:.1f} MB; must be under {mb:g} MB"))
    try:
        validate_image(data, ext="")
    except ImageFetchError:
        errors.append(FormError("cover", "cover file isn't a recognisable image"))
    return errors


def _check_content_upload(field: str, name: str, path: str | None) -> list[FormError]:
    """A content-page upload: a format the website takes, readable, and convertible."""
    label = UPLOAD_FIELDS[field]
    suffix = Path(name).suffix.lower()
    if suffix not in FORMATS:
        kind = f"{suffix} files" if suffix else "files without an extension"
        return [FormError(field, f"{label}: {name}: {kind} can't be used; save it as a Word (.docx), "
                                 "Markdown (.md), plain text (.txt) or HTML (.html) file")]
    try:
        check_content_file(path, label, field, shown_as=name)
        load_content(path, label)
    except PhoenixError as exc:
        return [FormError(field, str(exc))]
    return []


def _int_field(raw: RawForm, field: str, default: int) -> tuple[int, FormError | None]:
    text = getattr(raw, field).strip()
    if not text:
        return default, None
    try:
        return int(text), None
    except ValueError:
        return default, FormError(field, f"{field.replace('_', ' ')}: {text!r} isn't a whole number")


def _build_spec(raw: RawForm, slugs: list[str], blog: Blog | None) -> tuple[BookSpec, list[FormError]]:
    """Build the ``BookSpec`` plus any errors ``validate_spec`` can't catch (that
    function handles series/issn/isbn consistency and content-file existence).
    With no ``blog`` (the post list has errors) it has no source."""
    errors: list[FormError] = []

    image_max_width, mw_error = _int_field(raw, "image_max_width", DEFAULT_MAX_WIDTH)
    image_quality, iq_error = _int_field(raw, "image_quality", DEFAULT_QUALITY)
    errors.extend(e for e in (mw_error, iq_error) if e)

    source = blog and SourceSpec(
        platform=blog.platform,
        url=blog.url,
        processor=select_processor(blog.platform, extract_domain(blog.url), get_platform(blog.platform).default_processor),
        slugs=slugs,
    )
    spec = BookSpec(
        title=raw.title.strip() or "Collected Posts",
        subtitle=raw.subtitle.strip() or None,
        editor=raw.editor.strip() or None,
        series=raw.series.strip() or None,
        series_number=raw.series_number.strip() or None,
        issn=raw.issn.strip() or None,
        rights=raw.rights.strip() or None,
        publisher=raw.publisher.strip() or None,
        description=raw.description.strip() or None,
        pub_date=raw.pub_date.strip() or None,
        isbn=raw.isbn.strip() or None,
        lang=raw.lang.strip() or "en",
        cover=raw.paths.get("cover"),
        **{field: raw.paths.get(field) for field in CONTENT_FIELDS},
        placeholders=raw.placeholders,
        sort_names=_parse_kv_lines(raw.sort_names),
        alt_text=_parse_kv_lines(raw.alt_text),
        optimize_images=not raw.keep_original_images,
        image_max_width=image_max_width,
        image_quality=image_quality,
        # Somewhere writable for validate_spec's check (the app's folder is read-only on Vercel);
        # runs.run gives the real build its own temporary folder.
        output=str(Path(tempfile.gettempdir()) / "book.epub"),
        source=source,
    )
    return spec, errors


def check_submission(raw: RawForm, session: requests.Session | None = None,
                     progress: Callable[[Progress], None] | None = None) -> CheckResult:
    """Build a ``BookSpec`` from ``raw`` and run every pre-build check.

    Every problem is reported together in one pass, except that the live
    "does every post exist" check only runs once the post list itself is
    clean (a bad address list makes fetching meaningless). That check fetches
    the posts, so a clean result carries them, ready to build.
    """
    posts: list[Post] = []
    blog, slugs, post_errors = _check_post_list(raw.posts)
    spec, spec_errors = _build_spec(raw, slugs, blog)
    errors = post_errors + spec_errors

    if "cover" in raw.files:
        errors += _check_cover_bytes(raw.files["cover"][0])
    for field in CONTENT_FIELDS:
        if field in raw.files:
            if upload_errors := _check_content_upload(field, raw.files[field][1], raw.paths.get(field)):
                errors += upload_errors
                setattr(spec, field, None)  # already reported; validate_spec needn't trip over it again

    try:
        validate_spec(spec)
    except PhoenixError as exc:
        errors.append(FormError(exc.field, str(exc)))

    if not post_errors:
        try:
            platform = get_platform(spec.source.platform)
            secret = SecretStore().get(spec.source.platform, extract_domain(spec.source.url))
            posts = fetch_posts(spec.source, platform, secret, session or requests.Session(), progress)
        except PhoenixError as exc:
            errors.append(FormError(exc.field, str(exc)))

    return CheckResult(spec=spec, errors=errors, posts=posts)
