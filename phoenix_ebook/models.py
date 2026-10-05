"""Data passed between platforms, processors, the builder and the CLI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phoenix_ebook.images import DEFAULT_MAX_WIDTH, DEFAULT_QUALITY


@dataclass
class Author:
    """A post's author, as shown in the chapter byline."""

    name: str
    url: str | None = None  # profile page, linked from the byline


@dataclass
class Post:
    """One post from a platform, ready to become a chapter. See ``Platform.fetch_post``."""

    slug: str
    title: str
    html: str
    authors: list[Author] = field(default_factory=list)
    published_at: str | None = None  # ISO 8601, in the site's own timezone when known
    feature_image: str | None = None  # lead image URL, shown above the post body
    feature_image_alt: str | None = None
    feature_image_caption: str | None = None  # HTML
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceSpec:
    """Where a book's posts come from: platform, site URL, processor, and slugs in reading order."""

    platform: str
    url: str
    processor: str
    slugs: list[str] = field(default_factory=list)


@dataclass
class BookSpec:
    """Everything needed to build one book. Built from CLI flags or a TOML manifest.

    The manifest is also the shape the future web UI will produce, so keep the
    two in sync when adding fields. Content-file fields are paths to HTML
    fragments (the intro also accepts plain text).
    """

    title: str = "Collected Posts"
    author: str | None = None
    publisher: str | None = None
    description: str | None = None
    pub_date: str | None = None
    isbn: str | None = None
    lang: str = "en"

    cover: str | None = None
    copyright_file: str | None = None
    imprint_file: str | None = None
    foreword_file: str | None = None
    intro_file: str | None = None
    notes_file: str | None = None
    acknowledgements_file: str | None = None
    about_file: str | None = None
    placeholders: bool = True  # placeholder pages for front/back-matter sections without a file
    css: str | None = None  # user stylesheet, linked after the built-in ones

    optimize_images: bool = True
    image_max_width: int = DEFAULT_MAX_WIDTH  # 0 = no resizing
    image_quality: int = DEFAULT_QUALITY

    output: str = "book.epub"
    source: SourceSpec | None = None


@dataclass
class BuildProblem:
    """Something that went wrong during a build without stopping it."""

    kind: str  # e.g. "image-download-failed"
    post_slug: str | None
    url: str | None
    detail: str


@dataclass
class BuildResult:
    """What ``build()`` returns: the EPUB's path and any non-fatal problems."""

    path: str
    problems: list[BuildProblem] = field(default_factory=list)
