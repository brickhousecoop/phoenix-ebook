"""A build's warnings for the results page: grouped by post, each kind explained.

The explanations are the first paragraph of each kind's entry in
docs/troubleshooting.md, so the website and the command line say the same thing.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from phoenix_ebook.models import BuildProblem, Post

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "troubleshooting.md"
GUIDE_URL = "https://github.com/brickhousecoop/phoenix-ebook/blob/main/docs/troubleshooting.md"

LABELS = {
    "image-download-failed": "Image left out",
    "image-missing-alt": "No alt text",
    "image-suspicious-alt": "Unhelpful alt text",
    "alt-override-unused": "Alt-text entry not used",
    "call-to-action": "Call-to-action removed",
    "embed-removed": "Form or poll left out",
    "embed-unknown": "Unrecognised embed",
    "link-repaired": "Link repaired",
    "link-unlinked": "Broken link",
    "link-empty-removed": "Empty link removed",
    "content-image-left-out": "Images left out of a content page",
}


@dataclass
class Kind:
    code: str
    label: str
    meaning: str  # HTML
    more_url: str


@dataclass
class PostProblems:
    title: str  # "Whole book" for problems that aren't about one post
    problems: list[BuildProblem] = field(default_factory=list)


def kind(code: str) -> Kind:
    meaning, anchor = _guide_entries().get(code, ("", ""))
    return Kind(code, LABELS.get(code, code), meaning, f"{GUIDE_URL}#{anchor}" if anchor else GUIDE_URL)


def group_by_post(problems: list[BuildProblem], posts: list[Post]) -> list[PostProblems]:
    """Problems grouped by post, in reading order; book-wide ones last."""
    groups = {post.slug: PostProblems(post.title) for post in posts}
    book_wide = PostProblems("Whole book")
    for problem in problems:
        groups.get(problem.post_slug, book_wide).problems.append(problem)
    return [g for g in [*groups.values(), book_wide] if g.problems]


@cache
def _guide_entries() -> dict[str, tuple[str, str]]:
    """kind -> (first paragraph after its summary line, as HTML; heading anchor)."""
    entries = {}
    for code, body in re.findall(r"^### `([a-z-]+)`\n(.*?)(?=^#{2,3} |\Z)", GUIDE.read_text("utf-8"), re.M | re.S):
        paragraphs = []
        for paragraph in body.split("\n\n"):
            lines = [line for line in paragraph.strip().splitlines() if not line.startswith(("**", "- "))]
            if lines:
                paragraphs.append(" ".join(lines))
        entries[code] = (_inline_markdown(paragraphs[0]) if paragraphs else "", code)
    return entries


def _inline_markdown(text: str) -> str:
    """The Markdown the guide uses inside a paragraph: `code`, **bold**, *italic*, [links](…)."""
    out = html.escape(" ".join(text.split()), quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", out)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  lambda m: f'<a href="{GUIDE_URL + m[2] if m[2].startswith("#") else m[2]}">{m[1]}</a>', out)
