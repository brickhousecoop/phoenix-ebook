"""HTML processors: per-site cleanup of post HTML before it becomes a chapter, and their registry."""
from __future__ import annotations

from bs4 import BeautifulSoup, Comment, NavigableString

from phoenix_ebook.models import Post


_BR_TRIM_BLOCKS = ["p", "li", "figcaption", "blockquote"]
_BOLD = ("b", "strong")


def _meaningful_children(tag) -> list:
    return [c for c in tag.children if not (isinstance(c, NavigableString) and not c.strip())]


def _strip_web_markup(soup: BeautifulSoup) -> None:
    """Remove Ghost editor / web-only markup that means nothing in an ebook."""
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    for tag in soup.find_all(True):
        tag.attrs.pop("style", None)
        if tag.name == "img":
            tag.attrs.pop("loading", None)
            tag.attrs.pop("decoding", None)
        classes = [c for c in tag.get("class", []) if not c.startswith("kg-")]
        if classes:
            tag["class"] = classes
        else:
            tag.attrs.pop("class", None)

    # <br> as the first or last thing in a block is just editor spacing.
    for block in soup.find_all(_BR_TRIM_BLOCKS):
        for end in (0, -1):
            while (kids := _meaningful_children(block)) and kids[end].name == "br":
                kids[end].decompose()

    # Ghost captions nest emphasis: <b><strong>x</strong></b> -> <strong>x</strong>.
    for tag in soup.find_all(_BOLD):
        if tag.parent is None:  # already unwrapped into its parent
            continue
        collapsed = False
        while len(kids := _meaningful_children(tag)) == 1 and kids[0].name in _BOLD:
            for key, value in kids[0].attrs.items():
                tag.attrs.setdefault(key, value)
            kids[0].unwrap()
            collapsed = True
        if collapsed:
            tag.name = "strong"


class HtmlProcessor:
    """Cleans a post's HTML for the book. Subclass it for site-specific quirks.

    Set ``name`` (the ``--processor`` value), decorate with ``@register_processor``
    and import the module in ``processors/__init__.py``. A processor is picked from
    the site URL unless ``--processor`` is given.
    """

    name: str = "generic"

    def display_title(self, post: Post) -> str:
        """The chapter's title in the table of contents and the page ``<title>``."""
        return post.title

    def clean(self, soup: BeautifulSoup, post: Post) -> None:
        """Modify ``soup`` (the post body, plus its feature image figure) in place.

        Runs before images are fetched. Overrides must call ``super().clean()``
        first: this base implementation removes scripts/iframes/styles and all
        web-only editor markup (``kg-*`` classes, inline styles, comments, …).
        """
        for tag in soup.find_all(["script", "iframe", "style"]):
            tag.decompose()
        _strip_web_markup(soup)


class GenericProcessor(HtmlProcessor):
    """The default processor: only the shared cleanup."""

    name = "generic"


_REGISTRY: dict[str, type[HtmlProcessor]] = {}


def register_processor(cls: type[HtmlProcessor]) -> type[HtmlProcessor]:
    """Class decorator: make ``cls`` available as ``--processor cls.name``."""
    _REGISTRY[cls.name] = cls
    return cls


def get_processor(name: str) -> HtmlProcessor:
    """Return a new instance of the processor registered as ``name``."""
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise RuntimeError(f"Unknown processor: {name!r}. Registered: {sorted(_REGISTRY)}")


register_processor(GenericProcessor)
