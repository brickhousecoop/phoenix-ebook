"""HTML processors: per-site cleanup of post HTML before it becomes a chapter, and their registry."""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString

from phoenix_ebook.errors import InvalidBookSpec, SiteFileError
from phoenix_ebook.links import remove_empty_headings_and_ids, repair_links
from phoenix_ebook.models import Post
from phoenix_ebook.processors.rules import (SITES_DIR, SiteRules, read_site_file, load_site_rules, remove_buttons,
                                            remove_final_sections, remove_sales_lines, unlink_sales_links)


ANY_PLATFORM = "*"
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

    A blog whose rules are only lists needs no subclass, just a file in
    ``phoenix_ebook/sites/`` (see ``processors/rules.py``). For unusual logic, set
    ``name`` (the ``--processor`` value), ``platform`` and ``sites`` (substrings
    of the site's domain this processor handles), decorate with
    ``@register_processor``, import the module in ``processors/__init__.py``, and
    override ``apply_rules``. Platform dialects are not handled here but in
    ``Platform.normalize_html`` (ADR 0001); see ``select_processor`` for how one is
    chosen. Creating one raises ``SiteFileError`` if the blog's file has a mistake.
    """

    name: str = "generic"
    platform: str | None = None  # the platform whose sites this handles; None = generic
    sites: tuple[str, ...] = ()  # domain substrings, e.g. ("flaminghydra",)

    def __init__(self) -> None:
        self.rules: SiteRules = load_site_rules(self.name)

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
        changed link for the build report; see ``phoenix_ebook.links``), then
        applies the blog's rules (``apply_rules``).
        """
        for tag in soup.find_all(["script", "iframe", "style"]):
            tag.decompose()
        _strip_web_markup(soup)
        remove_empty_headings_and_ids(soup)
        repair_links(soup)
        self.apply_rules(soup, post)

    def apply_rules(self, soup: BeautifulSoup, post: Post) -> None:
        """Remove what this blog's rules say to: buttons, final sections, sales links, sales lines."""
        remove_buttons(soup, self.rules)
        remove_final_sections(soup, self.rules)
        unlink_sales_links(soup, self.rules)
        remove_sales_lines(soup, self.rules)


class GenericProcessor(HtmlProcessor):
    """The default processor: the shared cleanup and the default rules."""

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


def register_site_files(sites_dir: Path | None = None) -> None:
    """Make each file in ``phoenix_ebook/sites/`` a processor named after it, unless Python already
    registered that name. A file with a mistake still registers, under its own name and platform-free, so
    its blog's builds stop with the mistake (``get_processor`` raises) rather than losing the blog's rules."""
    sites_dir = sites_dir or SITES_DIR
    for path in sorted(sites_dir.glob("*.toml")):
        name = path.stem
        if name.startswith("_") or name in _REGISTRY:
            continue
        try:
            data = read_site_file(path)
            platform, sites = data.get("platform"), tuple(data.get("sites", ()))
            if not platform or not sites:
                raise SiteFileError(f"site rules file {path.name}: a blog with no Python needs platform and sites.")
        except SiteFileError as exc:
            _REGISTRY[name] = type(name, (_BrokenSiteProcessor,), {"name": name, "sites": (name,), "problem": exc})
            continue
        _REGISTRY[name] = type(name, (HtmlProcessor,), {"name": name, "platform": platform, "sites": sites})


class _BrokenSiteProcessor(HtmlProcessor):
    """A blog whose file can't be used: any platform may select it, and creating it raises the mistake."""

    platform = ANY_PLATFORM
    problem: SiteFileError

    def __init__(self) -> None:
        raise self.problem


def select_processor(platform: str, domain: str, default: str) -> str:
    """Name of the processor for a site: the first one registered for ``platform``
    whose ``sites`` match ``domain``, else ``default`` (the platform's default)."""
    for name, cls in _REGISTRY.items():
        if cls.platform in (platform, ANY_PLATFORM) and any(site in domain for site in cls.sites):
            return name
    return default
