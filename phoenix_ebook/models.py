from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phoenix_ebook.images import DEFAULT_MAX_WIDTH, DEFAULT_QUALITY


@dataclass
class Post:
    slug: str
    title: str
    html: str
    authors: list[str] = field(default_factory=list)
    published_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceSpec:
    platform: str
    url: str
    processor: str
    slugs: list[str] = field(default_factory=list)


@dataclass
class BookSpec:
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
    path: str
    problems: list[BuildProblem] = field(default_factory=list)
