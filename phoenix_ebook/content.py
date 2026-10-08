"""Content files (copyright, foreword, notes, …) -> the HTML of their page.

The format is chosen by the file's extension: HTML is inserted as written;
plain text, Markdown and Word files are converted to simple HTML (no styles).
A converted page whose first element isn't a heading gets the section's title
as its <h2>, and Markdown and Word headings move down a level, so "# Foreword"
or Word's "Heading 1" becomes that <h2> (the title page holds the book's only <h1>).
"""
from __future__ import annotations

import html
import zipfile
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from phoenix_ebook.errors import InvalidBookSpec

HTML_EXTENSIONS = (".html", ".htm", ".xhtml")
FORMATS = (".html", ".txt", ".md", ".docx")  # what to offer people; HTML_EXTENSIONS also work
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass
class ContentPage:
    html: str
    images_left_out: int = 0  # images in a Word file, which aren't carried over yet


def load_content(path: str, title: str, *, guess_text: bool = False) -> ContentPage:
    """The page for the content file at ``path``, titled ``title``.

    Files with an extension phoenix-ebook doesn't know are read as before
    content formats existed: as HTML, or, with ``guess_text`` (the
    introduction), as plain text unless they start with "<".
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return _from_docx(path, title)
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return ContentPage("")
    if suffix == ".txt":
        return ContentPage(text_as_html(title, text))
    if suffix == ".md":
        from markdown_it import MarkdownIt
        # Raw HTML off: "<club>" in a sentence stays text, rather than an element the book can't contain.
        return ContentPage(_tidy(MarkdownIt("commonmark", {"html": False}).render(text), title))
    if suffix in HTML_EXTENSIONS or not guess_text or text.startswith("<"):
        return ContentPage(text)
    return ContentPage(text_as_html(title, text))


def check_content_file(path: str, option: str, field: str, shown_as: str | None = None) -> None:
    """Raise InvalidBookSpec if the content file at ``path`` can't be read in its format.

    For the message: ``option`` is how the user named the setting, ``field`` the
    BookSpec field, and ``shown_as`` the file's name if not ``path`` (an upload).
    """
    name = shown_as or path
    if Path(path).suffix.lower() == ".docx":
        if not zipfile.is_zipfile(path):
            raise InvalidBookSpec(f"{option}: {name} isn't a Word file (.docx); save it from Word as .docx "
                                  "(not .doc), or use .txt, .md or .html", field=field)
        return
    try:
        Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise InvalidBookSpec(f"{option}: {name} isn't UTF-8 text; save it as UTF-8 "
                              "(most editors offer this under 'Save As' or 'Encoding')", field=field) from None


def text_as_html(title: str, text: str) -> str:
    """Plain text as an <h2> title plus one paragraph per non-empty line."""
    paragraphs = "".join(f"<p>{html.escape(line.strip(), quote=False)}</p>"
                         for line in text.splitlines() if line.strip())
    return f"<h2>{html.escape(title, quote=False)}</h2>\n{paragraphs}"


def _from_docx(path: str, title: str) -> ContentPage:
    import mammoth

    try:
        with open(path, "rb") as f:
            converted = mammoth.convert_to_html(f).value
    except Exception as exc:
        raise InvalidBookSpec(f"The Word file {Path(path).name} couldn't be read ({exc}); open it in Word, "
                              "save it again as .docx, and retry") from None
    soup = BeautifulSoup(converted, "html.parser")
    images = soup.find_all("img")
    for img in images:
        img.decompose()
    for paragraph in soup.find_all("p"):  # a paragraph that only held an image
        if not paragraph.get_text(strip=True) and not paragraph.find(True):
            paragraph.decompose()
    return ContentPage(_tidy(str(soup), title), images_left_out=len(images))


def _tidy(fragment: str, title: str) -> str:
    """Headings down a level, and the section title added if the page doesn't start with one."""
    soup = BeautifulSoup(fragment, "html.parser")
    for heading in soup.find_all(HEADINGS):
        heading.name = f"h{min(int(heading.name[1]) + 1, 6)}"
    first = soup.find(True)
    if first is None or first.name not in HEADINGS:
        heading = soup.new_tag("h2")
        heading.string = title
        soup.insert(0, heading)
    return str(soup).strip()
