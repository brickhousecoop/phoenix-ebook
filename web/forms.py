"""Turns a submitted book form into a ``BookSpec`` and runs the pre-build checks.

Kept free of FastAPI imports so it's testable as plain Python; ``main.py`` adapts
FastAPI's request objects to the plain values this module works with.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import requests

from phoenix_ebook.epub_builder import fetch_posts, validate_spec
from phoenix_ebook.errors import PhoenixError
from phoenix_ebook.images import DEFAULT_MAX_WIDTH, DEFAULT_QUALITY, ImageFetchError, validate_image
from phoenix_ebook.models import BookSpec, SourceSpec
from phoenix_ebook.platforms.base import get_platform
from phoenix_ebook.processors.base import select_processor
from phoenix_ebook.secrets import SecretStore, extract_domain

PLATFORM = "ghost"
SITE_URL = "https://flaminghydra.ghost.io"
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
    """The submitted field values, as strings (and the cover, already saved to disk).

    Mirrors ``BookSpec`` field-for-field (the "simple" fields plus everything
    under "advanced"); see ``build_epub.py``'s ``_spec_from_args`` for the CLI's
    equivalent mapping. Every field is the raw text a browser would submit —
    blank means "not given".
    """

    posts: str = ""
    title: str = ""
    subtitle: str = ""
    editor: str = ""
    cover_path: str | None = None  # already written to disk by the caller
    cover_bytes: bytes | None = None  # the same cover's raw bytes, for the image/size check

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

    @property
    def ok(self) -> bool:
        return not self.errors

    def errors_for(self, field: str) -> list[str]:
        return [e.message for e in self.errors if e.field == field]


def parse_post_addresses(text: str) -> list[tuple[int, str]]:
    """Non-blank, stripped lines from the textarea, with their 1-based line numbers."""
    return [(i, line) for i, raw in enumerate(text.splitlines(), start=1) if (line := raw.strip())]


def _parse_kv_lines(text: str) -> dict[str, str]:
    """"key = value" per line (blank lines ignored) -> a dict, last one wins."""
    result: dict[str, str] = {}
    for _, line in parse_post_addresses(text):
        key, _, value = line.partition("=")
        if key.strip():
            result[key.strip()] = value.strip()
    return result


def _check_post_list(raw_text: str) -> tuple[list[str], list[FormError]]:
    """Resolve addresses to slugs, in order; report unrecognised addresses, duplicates
    (with line numbers) and more than ``MAX_POSTS`` posts."""
    lines = parse_post_addresses(raw_text)
    if not lines:
        return [], [FormError("posts", "add at least one post address")]

    platform = get_platform(PLATFORM)
    errors: list[FormError] = []
    seen: dict[str, int] = {}
    slugs: list[str] = []
    unrecognised: list[int] = []
    duplicates: list[str] = []

    for lineno, address in lines:
        slug = platform.parse_address(address)
        if slug is None:
            unrecognised.append(lineno)
            continue
        if slug in seen:
            duplicates.append(f"line {lineno} repeats line {seen[slug]} ({slug!r})")
            continue
        seen[slug] = lineno
        slugs.append(slug)

    if unrecognised:
        where = ", ".join(f"line {n}" for n in unrecognised)
        errors.append(FormError("posts", f"not a flaminghydra.com (or flaminghydra.ghost.io) address or a bare "
                                         f"slug: {where}"))
    if duplicates:
        errors.append(FormError("posts", f"duplicate posts: {'; '.join(duplicates)}"))
    if len(slugs) > MAX_POSTS:
        errors.append(FormError("posts", f"{len(slugs)} posts given; this website builds at most {MAX_POSTS} "
                                         "at a time"))
    return slugs, errors


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


def _int_field(raw: RawForm, field: str, default: int) -> tuple[int, FormError | None]:
    text = getattr(raw, field).strip()
    if not text:
        return default, None
    try:
        return int(text), None
    except ValueError:
        return default, FormError(field, f"{field.replace('_', ' ')}: {text!r} isn't a whole number")


def _build_spec(raw: RawForm, slugs: list[str]) -> tuple[BookSpec, list[FormError]]:
    """Build the ``BookSpec`` plus any errors ``validate_spec`` can't catch (that
    function handles series/issn/isbn consistency and content-file existence)."""
    errors: list[FormError] = []

    image_max_width, mw_error = _int_field(raw, "image_max_width", DEFAULT_MAX_WIDTH)
    image_quality, iq_error = _int_field(raw, "image_quality", DEFAULT_QUALITY)
    errors.extend(e for e in (mw_error, iq_error) if e)

    source = SourceSpec(
        platform=PLATFORM,
        url=SITE_URL,
        processor=select_processor(PLATFORM, extract_domain(SITE_URL), get_platform(PLATFORM).default_processor),
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
        cover=raw.cover_path,
        placeholders=raw.placeholders,
        sort_names=_parse_kv_lines(raw.sort_names),
        alt_text=_parse_kv_lines(raw.alt_text),
        optimize_images=not raw.keep_original_images,
        image_max_width=image_max_width,
        image_quality=image_quality,
        source=source,
    )
    return spec, errors


def check_submission(raw: RawForm) -> CheckResult:
    """Build a ``BookSpec`` from ``raw`` and run every pre-build check.

    Every problem is reported together in one pass, except that the live
    "does every post exist" check only runs once the post list itself is
    clean (a bad address list makes fetching meaningless).
    """
    slugs, post_errors = _check_post_list(raw.posts)
    spec, spec_errors = _build_spec(raw, slugs)
    errors = post_errors + spec_errors

    if raw.cover_bytes is not None:
        errors += _check_cover_bytes(raw.cover_bytes)

    try:
        validate_spec(spec)
    except PhoenixError as exc:
        errors.append(FormError(exc.field, str(exc)))

    if not post_errors:
        try:
            platform = get_platform(spec.source.platform)
            secret = SecretStore().get(spec.source.platform, extract_domain(spec.source.url))
            fetch_posts(spec.source, platform, secret, requests.Session())
        except PhoenixError as exc:
            errors.append(FormError(exc.field, str(exc)))

    return CheckResult(spec=spec, errors=errors)
