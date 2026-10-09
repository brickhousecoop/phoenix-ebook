#!/usr/bin/env python3
"""Command-line entry point: turn CLI flags or a TOML manifest into a BookSpec and build it."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import traceback
from pathlib import Path

try:
    import tomllib
except ImportError:
    tomllib = None

import requests

from phoenix_ebook import platforms, processors  # noqa: F401 — ensures registrations run
from phoenix_ebook.epub_builder import build, fetch_posts, validate_spec
from phoenix_ebook.errors import InvalidBookSpec, ManifestError, PhoenixError
from phoenix_ebook.images import DEFAULT_MAX_WIDTH, DEFAULT_QUALITY
from phoenix_ebook.models import BookSpec, SourceSpec
from phoenix_ebook.platforms.base import get_platform
from phoenix_ebook.processors.base import get_processor, select_processor
from phoenix_ebook.secrets import SecretStore, extract_domain


def _default_processor_for(platform: str, url: str) -> str:
    """The site processor registered for this platform and site, else the platform's default."""
    return select_processor(platform, extract_domain(url), get_platform(platform).default_processor)


def _one_editor(editor: str | None, author: str | None, names: str) -> str | None:
    """Resolve the editor from its two spellings; both given and different is an error."""
    if editor and author and editor != author:
        raise InvalidBookSpec(f"{names} disagree ({editor!r} vs {author!r}); give just one")
    return editor or author


def _spec_from_args(args) -> BookSpec:
    source = SourceSpec(
        platform=args.platform,
        url=args.url,
        processor=args.processor or _default_processor_for(args.platform, args.url),
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
        raise ManifestError("TOML manifests need Python 3.11 or newer")
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"manifest not found: {path}") from None
    except UnicodeDecodeError:
        raise ManifestError(f"{path} isn't UTF-8 text; save it as UTF-8") from None
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path} isn't valid TOML: {exc}") from None

    book = data.get("book", {})
    source_data = data.get("source", {})
    content = data.get("content", {})
    images = data.get("images", {})
    style = data.get("style", {})
    slugs = data.get("posts") or source_data.get("slugs") or []

    if not source_data.get("platform") or not source_data.get("url"):
        raise ManifestError(f'{path}: a [source] table with platform and url is required, e.g.\n'
                            '  [source]\n  platform = "ghost"\n  url = "https://<site>.ghost.io"')
    source = SourceSpec(
        platform=source_data["platform"],
        url=source_data["url"],
        processor=source_data.get("processor") or _default_processor_for(source_data["platform"], source_data["url"]),
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
        accessibility_summary=book.get("accessibility_summary"),
        sort_names=dict(data.get("sort_names", {})),
        alt_text=dict(data.get("alt_text", {})),
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
    posts = fetch_posts(spec.source, platform, secret, session)

    result = build(spec, posts, processor, session=session, image_base_url=spec.source.url, platform=platform)
    print(f"wrote {result.path}")
    _report_problems(result.problems)


TROUBLESHOOTING_URL = "https://github.com/brickhousecoop/phoenix-ebook/blob/main/docs/troubleshooting.md"


def _report_problems(problems) -> None:
    if not problems:
        return
    for p in problems:
        where = f"[{p.post_slug}] {p.location}: " if p.location else (f"[{p.post_slug}] " if p.post_slug else "")
        caption = f' (caption: "{p.caption}")' if p.caption else ""
        print(f"warning: {where}{p.url}: {p.detail}{caption}", file=sys.stderr)

    def count(kind):
        return sum(p.kind == kind for p in problems)

    def plural(n, one, many):
        return one if n == 1 else many

    if n := count("image-download-failed"):
        print(f"{n} {plural(n, 'image', 'images')} could not be downloaded and "
              f"{plural(n, 'was', 'were')} left out; see warnings above", file=sys.stderr)
    if n := count("image-missing-alt"):
        print(f"{n} {plural(n, 'image has', 'images have')} no alt text", file=sys.stderr)
    if n := count("image-suspicious-alt"):
        print(f"{n} {plural(n, 'image has', 'images have')} alt text that looks unhelpful", file=sys.stderr)
    if n := count("call-to-action"):
        print(f"{n} website call{'' if n == 1 else 's'}-to-action (subscribe/support) "
              f"{'was' if n == 1 else 'were'} removed, unlinked or kept; review the warnings above", file=sys.stderr)
    if n := count("embed-removed"):
        print(f"{n} embedded {plural(n, 'form or poll was', 'forms or polls were')} left out; "
              "see warnings above", file=sys.stderr)
    if n := count("embed-unknown"):
        print(f"{n} unrecognised {plural(n, 'embed', 'embeds')} became {plural(n, 'a link card', 'link cards')}; "
              "see warnings above", file=sys.stderr)
    if n := count("link-repaired"):
        print(f"{n} {plural(n, 'link', 'links')} in posts {plural(n, 'was', 'were')} repaired; "
              "check the warnings above", file=sys.stderr)
    if n := count("link-unlinked"):
        print(f"{n} {plural(n, 'link', 'links')} in posts pointed nowhere and {plural(n, 'was', 'were')} unlinked "
              "(words kept); see warnings above", file=sys.stderr)
    if n := count("link-empty-removed"):
        print(f"{n} empty {plural(n, 'link was', 'links were')} removed; see warnings above", file=sys.stderr)
    if n := count("content-image-left-out"):
        print(f"{n} {plural(n, 'content file had images', 'content files had images')} that "
              f"{plural(n, 'was', 'were')} left out; see warnings above", file=sys.stderr)
    if n := count("alt-override-unused"):
        print(f"{n} [alt_text] {plural(n, 'entry matches', 'entries match')} no image in the book", file=sys.stderr)

    print(f"What these warnings mean: {TROUBLESHOOTING_URL}", file=sys.stderr)
    _print_alt_text_block(problems)  # last, so it can be copied to the end of the output


def _print_alt_text_block(problems) -> None:
    to_fix = [p for p in problems if p.kind in ("image-missing-alt", "image-suspicious-alt")]
    if to_fix:
        print('\n# Add to your manifest and fill in (leave "" only for purely decorative images):\n# [alt_text]',
              file=sys.stderr)
        seen = set()
        for p in to_fix:
            if p.url in seen:
                continue
            seen.add(p.url)
            caption = f': "{p.caption}"' if p.caption else ""
            print(f'# "{p.url}" = ""   # {p.post_slug}, {p.location}{caption}', file=sys.stderr)


def _cmd_set_secret(args, *, prefer_keyring: bool) -> None:
    if not args.url and not args.domain:
        raise PhoenixError("--set-secret needs --url (the site) or --domain")
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
    parser.add_argument("--debug", action="store_true", help="Show full tracebacks for errors")
    parser.add_argument("slugs", nargs="*", help="Post slugs to include")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the CLI: store a secret (--set-secret[-file]) or build a book."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.set_secret_file or args.set_secret:
            _cmd_set_secret(args, prefer_keyring=args.set_secret and not args.set_secret_file)
            return
        if not args.manifest and not args.slugs:
            parser.error("provide post slugs or use --manifest")
        spec = _spec_from_manifest(args.manifest) if args.manifest else _spec_from_args(args)
        _run_build(spec, override_secret=args.admin_key)
    except PhoenixError as exc:
        if args.debug:
            traceback.print_exc()
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        print("error: unexpected failure, which is a bug in phoenix-ebook. "
              "Please report it with the traceback above.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
