"""HTML processors: per-site cleanup of post HTML before it becomes a chapter, and their registry."""
from __future__ import annotations

from bs4 import BeautifulSoup, Comment, NavigableString

from phoenix_ebook.errors import InvalidBookSpec
from phoenix_ebook.links import remove_empty_headings_and_ids, repair_links
from phoenix_ebook.models import Post


_BR_TRIM_BLOCKS = ["p", "li", "figcaption", "blockquote"]
_BOLD = ("b", "strong")


def _meaningful_children(tag) -> list:
    return [c for c in tag.children if not (isinstance(c, NavigableString) and not c.strip())]


def _strip_web_markup(soup: BeautifulSoup) -> None:
    """Remove web-only markup that means nothing in an ebook, whatever the platform.

    Platform-specific dialects (e.g. Ghost's ``kg-*`` classes) are handled by the
    platform's ``normalize_html`` before this runs (ADR 0001).
    """
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    for tag in soup.find_all(True):
        tag.attrs.pop("style", None)
        if tag.name == "img":
            tag.attrs.pop("loading", None)
            tag.attrs.pop("decoding", None)
        if "class" in tag.attrs and not tag["class"]:
            del tag["class"]

    # <br> as the first or last thing in a block is just editor spacing.
    for block in soup.find_all(_BR_TRIM_BLOCKS):
        for end in (0, -1):
            while (kids := _meaningful_children(block)) and kids[end].name == "br":
                kids[end].decompose()

    # Editors often nest emphasis: <b><strong>x</strong></b> -> <strong>x</strong>.
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
    """Adjusts a post's canonical HTML for the book. Subclass it for one site's quirks.

    Set ``name`` (the ``--processor`` value), ``platform`` and ``sites`` (substrings
    of the site's domain this processor handles), decorate with
    ``@register_processor`` and import the module in ``processors/__init__.py``.
    Platform dialects are not handled here but in ``Platform.normalize_html``
    (ADR 0001); see ``select_processor`` for how one is chosen.
    """

    name: str = "generic"
    platform: str | None = None  # the platform whose sites this handles; None = generic
    sites: tuple[str, ...] = ()  # domain substrings, e.g. ("flaminghydra",)

    def display_title(self, post: Post) -> str:
        """The chapter's label on the contents page and in the app's contents menu.

        Defaults to the plain post title. Override to add e.g. the author. The
        chapter's own heading and page ``<title>`` always use the plain title.
        """
        return post.title

    def clean(self, soup: BeautifulSoup, post: Post) -> None:
        """Modify ``soup`` (the post body, plus its feature image figure) in place.

        Receives canonical chapter HTML. Runs before images are fetched. Overrides
        must call ``super().clean()`` first: this base implementation removes
        scripts/iframes/styles and web-only markup (inline styles, comments, …),
        empty headings and ids, and repairs or unlinks broken links (marking each
        changed link for the build report; see ``phoenix_ebook.links``).
        """
        for tag in soup.find_all(["script", "iframe", "style"]):
            tag.decompose()
        _strip_web_markup(soup)
        remove_empty_headings_and_ids(soup)
        repair_links(soup)


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
        raise InvalidBookSpec(f"unknown processor {name!r}; available: {', '.join(sorted(_REGISTRY))}") from None


register_processor(GenericProcessor)


def select_processor(platform: str, domain: str, default: str) -> str:
    """Name of the processor for a site: the first one registered for ``platform``
    whose ``sites`` match ``domain``, else ``default`` (the platform's default)."""
    for name, cls in _REGISTRY.items():
        if cls.platform == platform and any(site in domain for site in cls.sites):
            return name
    return default
