#!/usr/bin/env python3
"""Command-line entry point: turn CLI flags or a TOML manifest into a BookSpec and build it."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    tomllib = None

import requests

from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.epub_builder import InvalidBookSpec, build, validate_spec
from phoenix_ebook.images import DEFAULT_MAX_WIDTH, DEFAULT_QUALITY
from phoenix_ebook.models import BookSpec, SourceSpec
from phoenix_ebook.platforms.base import get_platform
from phoenix_ebook.processors.base import get_processor
from phoenix_ebook.secrets import SecretStore, extract_domain


def _default_processor_for(url: str) -> str:
    domain = extract_domain(url)
    if "flaminghydra" in domain:
        return "flaminghydra"
    return "generic"


def _one_editor(editor: str | None, author: str | None, names: str) -> str | None:
    """Resolve the editor from its two spellings; both given and different is an error."""
    if editor and author and editor != author:
        raise InvalidBookSpec(f"{names} disagree ({editor!r} vs {author!r}); give just one")
    return editor or author


def _spec_from_args(args) -> BookSpec:
    source = SourceSpec(
        platform=args.platform,
        url=args.url,
        processor=args.processor or _default_processor_for(args.url),
        slugs=list(args.slugs),
    )
    return BookSpec(
        title=args.title,
        subtitle=args.subtitle,
        editor=_one_editor(args.editor, args.author, "--editor and --author"),
        series=args.series,
        series_number=args.series_number,
        issn=args.issn,
        rights=args.rights,
        publisher=args.publisher,
        description=args.description,
        pub_date=args.pub_date,
        isbn=args.isbn,
        lang=args.lang,
        cover=args.cover,
        copyright_file=args.copyright_file,
        imprint_file=args.imprint_file,
        foreword_file=args.foreword_file,
        intro_file=args.intro_file,
        notes_file=args.notes_file,
        acknowledgements_file=args.acknowledgements_file,
        about_file=args.about_file,
        placeholders=not args.no_placeholders,
        css=args.css,
        optimize_images=not args.keep_original_images,
        image_max_width=args.image_max_width,
        image_quality=args.image_quality,
        output=args.output,
        source=source,
    )


def _spec_from_manifest(path: str) -> BookSpec:
    if tomllib is None:
        raise RuntimeError("TOML manifest requires Python 3.11+ (tomllib)")
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    book = data.get("book", {})
    source_data = data.get("source", {})
    content = data.get("content", {})
    images = data.get("images", {})
    style = data.get("style", {})
    slugs = data.get("posts") or source_data.get("slugs") or []

    source = SourceSpec(
        platform=source_data["platform"],
        url=source_data["url"],
        processor=source_data.get("processor") or _default_processor_for(source_data["url"]),
        slugs=list(slugs),
    )
    return BookSpec(
        title=book.get("title", "Collected Posts"),
        subtitle=book.get("subtitle"),
        editor=_one_editor(book.get("editor"), book.get("author"), "[book] editor and author"),
        series=book.get("series"),
        series_number=str(book["series_number"]) if "series_number" in book else None,
        issn=book.get("issn"),
        rights=book.get("rights"),
        sort_names=dict(data.get("sort_names", {})),
        publisher=book.get("publisher"),
        description=book.get("description"),
        pub_date=book.get("pub_date"),
        isbn=book.get("isbn"),
        lang=book.get("lang", "en"),
        cover=content.get("cover"),
        copyright_file=content.get("copyright_file"),
        imprint_file=content.get("imprint_file"),
        foreword_file=content.get("foreword_file"),
        intro_file=content.get("intro_file"),
        notes_file=content.get("notes_file"),
        acknowledgements_file=content.get("acknowledgements_file"),
        about_file=content.get("about_file"),
        placeholders=content.get("placeholders", True),
        css=style.get("css"),
        optimize_images=images.get("optimize", True),
        image_max_width=images.get("max_width", DEFAULT_MAX_WIDTH),
        image_quality=images.get("quality", DEFAULT_QUALITY),
        output=book.get("output", "book.epub"),
        source=source,
    )


def _run_build(spec: BookSpec, override_secret: str | None) -> None:
    assert spec.source is not None
    validate_spec(spec)  # before any network access, so mistakes fail fast
    store = SecretStore(override=override_secret)
    secret = store.get(spec.source.platform, extract_domain(spec.source.url))

    platform = get_platform(spec.source.platform)
    processor = get_processor(spec.source.processor)

    session = requests.Session()
    posts = [
        platform.fetch_post(session, spec.source.url, secret, slug)
        for slug in spec.source.slugs
    ]

    result = build(spec, posts, processor, session=session, image_base_url=spec.source.url)
    print(f"wrote {result.path}")
    _report_problems(result.problems)


def _report_problems(problems) -> None:
    if not problems:
        return
    for p in problems:
        print(f"warning: [{p.post_slug}] {p.url}: {p.detail}", file=sys.stderr)
    failed = sum(p.kind == "image-download-failed" for p in problems)
    if failed:
        noun, verb = ("image", "was") if failed == 1 else ("images", "were")
        print(f"{failed} {noun} could not be downloaded and {verb} left out; see warnings above",
              file=sys.stderr)


def _cmd_set_secret(args, *, prefer_keyring: bool) -> None:
    if not args.url and not args.domain:
        raise RuntimeError("Provide --url or --domain")
    domain = extract_domain(args.url) if args.url else args.domain

    secret = os.environ.get("PHOENIX_SET_SECRET_VALUE") or getpass.getpass(
        f"Enter secret for platform={args.platform} domain={domain}: "
    ).strip()

    where = SecretStore().set(args.platform, domain, secret, prefer_keyring=prefer_keyring)
    print(f"Stored secret for platform={args.platform} domain={domain} in {where}")


def build_parser() -> argparse.ArgumentParser:
    """All CLI flags. Existing flags must keep working (users have saved invocations)."""
    parser = argparse.ArgumentParser(description="Build an EPUB from posts fetched from a platform.")
    parser.add_argument("--url", default="https://flaminghydra.ghost.io", help="Site URL")
    parser.add_argument("--platform", default="ghost", help="Source platform (default: ghost)")
    parser.add_argument("--processor", help="HTML processor (default: inferred from URL)")
    parser.add_argument("--admin-key", help="Platform API secret (id:secret for Ghost)")
    parser.add_argument("--set-secret", action="store_true", help="Store a secret in keyring (fallback: file)")
    parser.add_argument("--set-secret-file", action="store_true", help="Store a secret in local JSON file")
    parser.add_argument("--domain", help="Domain for --set-secret (defaults to --url host)")

    parser.add_argument("--manifest", help="Path to a TOML manifest describing the book")

    # metadata
    parser.add_argument("--title", default="Collected Posts", help="Book title")
    parser.add_argument("--editor", help="Who compiled the book; credited as its creator (post authors become contributors)")
    parser.add_argument("--author", help="Old name for --editor (still accepted)")
    parser.add_argument("--subtitle", help="Book subtitle")
    parser.add_argument("--series", help="Series the book belongs to, e.g. 'Flaming Hydra Digest'")
    parser.add_argument("--series-number", help="The book's number in the series (requires --series)")
    parser.add_argument("--issn", help="ISSN of the series (requires --series); one number for all issues")
    parser.add_argument("--rights", help="Rights statement for the book's metadata (omitted if not given)")
    parser.add_argument("--publisher", help="Publisher name")
    parser.add_argument("--description", help="Book description")
    parser.add_argument("--pub-date", help="Publication date, e.g. 2026-09-29")
    parser.add_argument("--isbn", help="ISBN / book identifier")
    parser.add_argument("--lang", default="en", help="Language code")

    # content files
    parser.add_argument("--cover", help="Path to cover image (optional)")
    parser.add_argument("--copyright-file", help="Path to copyright HTML/text file")
    parser.add_argument("--imprint-file", help="Path to imprint HTML/text file")
    parser.add_argument("--foreword-file", help="Path to foreword HTML/text file")
    parser.add_argument("--intro-file", help="Path to intro HTML/text file")
    parser.add_argument("--notes-file", help="Path to notes HTML/text file")
    parser.add_argument("--acknowledgements-file", help="Path to acknowledgements HTML/text file")
    parser.add_argument("--about-file", help="Path to About This Book HTML/text file")
    parser.add_argument("--no-placeholders", action="store_true",
                        help="Leave out front/back-matter sections that have no file, instead of a placeholder page")

    # styling
    parser.add_argument("--css", help="Extra CSS file, applied after the built-in styles (its rules win)")

    # images
    parser.add_argument("--image-max-width", type=int, default=DEFAULT_MAX_WIDTH,
                        help=f"Scale post images down to this width in px; 0 = no resizing (default: {DEFAULT_MAX_WIDTH})")
    parser.add_argument("--image-quality", type=int, default=DEFAULT_QUALITY,
                        help=f"JPEG quality for re-encoded images, 1-95 (default: {DEFAULT_QUALITY})")
    parser.add_argument("--keep-original-images", action="store_true",
                        help="Embed post images exactly as downloaded (no resizing or conversion)")

    parser.add_argument("--output", default="book.epub", help="Output EPUB path")
    parser.add_argument("slugs", nargs="*", help="Post slugs to include")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the CLI: store a secret (--set-secret[-file]) or build a book."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.set_secret_file:
        _cmd_set_secret(args, prefer_keyring=False)
        return
    if args.set_secret:
        _cmd_set_secret(args, prefer_keyring=True)
        return

    if not args.manifest and not args.slugs:
        parser.error("provide post slugs or use --manifest")
    try:
        spec = _spec_from_manifest(args.manifest) if args.manifest else _spec_from_args(args)
        _run_build(spec, override_secret=args.admin_key)
    except InvalidBookSpec as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
