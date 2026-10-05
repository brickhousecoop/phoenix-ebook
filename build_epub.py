#!/usr/bin/env python3
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
from phoenix_ebook.epub_builder import build
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


def _spec_from_args(args) -> BookSpec:
    source = SourceSpec(
        platform=args.platform,
        url=args.url,
        processor=args.processor or _default_processor_for(args.url),
        slugs=list(args.slugs),
    )
    return BookSpec(
        title=args.title,
        author=args.author,
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
    slugs = data.get("posts") or source_data.get("slugs") or []

    source = SourceSpec(
        platform=source_data["platform"],
        url=source_data["url"],
        processor=source_data.get("processor") or _default_processor_for(source_data["url"]),
        slugs=list(slugs),
    )
    return BookSpec(
        title=book.get("title", "Collected Posts"),
        author=book.get("author"),
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
        optimize_images=images.get("optimize", True),
        image_max_width=images.get("max_width", DEFAULT_MAX_WIDTH),
        image_quality=images.get("quality", DEFAULT_QUALITY),
        output=book.get("output", "book.epub"),
        source=source,
    )


def _run_build(spec: BookSpec, override_secret: str | None) -> None:
    assert spec.source is not None
    store = SecretStore(override=override_secret)
    secret = store.get(spec.source.platform, extract_domain(spec.source.url))

    platform = get_platform(spec.source.platform)
    processor = get_processor(spec.source.processor)

    session = requests.Session()
    posts = [
        platform.fetch_post(session, spec.source.url, secret, slug)
        for slug in spec.source.slugs
    ]

    out = build(spec, posts, processor, session=session, image_base_url=spec.source.url)
    print(f"wrote {out}")


def _cmd_set_secret(args, *, prefer_keyring: bool) -> None:
    if not args.url and not args.domain:
        raise RuntimeError("Provide --url or --domain")
    domain = extract_domain(args.url) if args.url else args.domain

    secret = os.environ.get("PHOENIX_SET_SECRET_VALUE") or getpass.getpass(
        f"Enter secret for platform={args.platform} domain={domain}: "
    ).strip()

    where = SecretStore().set(args.platform, domain, secret, prefer_keyring=prefer_keyring)
    print(f"Stored secret for platform={args.platform} domain={domain} in {where}")


def main() -> None:
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
    parser.add_argument("--author", help="Book author / editor")
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

    # images
    parser.add_argument("--image-max-width", type=int, default=DEFAULT_MAX_WIDTH,
                        help=f"Scale post images down to this width in px; 0 = no resizing (default: {DEFAULT_MAX_WIDTH})")
    parser.add_argument("--image-quality", type=int, default=DEFAULT_QUALITY,
                        help=f"JPEG quality for re-encoded images, 1-95 (default: {DEFAULT_QUALITY})")
    parser.add_argument("--keep-original-images", action="store_true",
                        help="Embed post images exactly as downloaded (no resizing or conversion)")

    parser.add_argument("--output", default="book.epub", help="Output EPUB path")
    parser.add_argument("slugs", nargs="*", help="Post slugs to include")
    args = parser.parse_args()

    if args.set_secret_file:
        _cmd_set_secret(args, prefer_keyring=False)
        return
    if args.set_secret:
        _cmd_set_secret(args, prefer_keyring=True)
        return

    if args.manifest:
        spec = _spec_from_manifest(args.manifest)
    else:
        if not args.slugs:
            parser.error("provide post slugs or use --manifest")
        spec = _spec_from_args(args)

    _run_build(spec, override_secret=args.admin_key)


if __name__ == "__main__":
    main()
