"""Alt text checks and overrides for images in chapters (#10)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_GENERIC_WORDS = {"image", "photo", "picture", "img", "graphic", "screenshot"}
_FILE_NAME_PATTERNS = [
    re.compile(r"\.(jpe?g|png|gif|webp|svg|tiff?|heic|avif)$", re.I),       # ends in an image extension
    re.compile(r"^(IMG|DSC|DSCN|PXL|DCIM)[_-]?\d+", re.I),                    # camera file names
    re.compile(r"^screen ?shot[ _-]?\d{4}[-_]\d{2}", re.I),                  # "Screenshot 2026-08-03 …"
]


def normalize_image_url(url: str) -> str:
    """The generic override key: the URL without query string or fragment.

    Platforms add their own rules on top (``Platform.canonical_image_url``), e.g.
    Ghost's size variants.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def suspicious_alt(alt: str, caption: str | None) -> str | None:
    """Why ``alt`` looks useless to a screen-reader user, or None if it looks fine."""
    text = alt.strip()
    if any(p.search(text) for p in _FILE_NAME_PATTERNS):
        return f"alt text looks like a file name: {text!r}"
    if text.casefold() in _GENERIC_WORDS:
        return f"alt text is only a generic word: {text!r}"
    if caption and _normalized(text) == _normalized(caption):
        return "alt text repeats the caption (screen readers would read it twice)"
    return None
