"""Source platforms: where posts come from (Ghost today), and their registry."""
from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from phoenix_ebook.models import Post


class Platform(ABC):
    """A publishing platform phoenix-ebook can fetch posts from.

    Subclass it, set ``name`` (the ``--platform`` value), decorate the class with
    ``@register_platform``, and import the module in ``platforms/__init__.py`` so
    registration runs. The builder never talks to a platform's API directly; the
    ``Post`` returned here is all it sees.
    """

    name: str

    @abstractmethod
    def fetch_post(self, session: requests.Session, url: str, secret: str, slug: str) -> Post:
        """Fetch the post identified by ``slug`` from the site at ``url``.

        Use ``session`` for all HTTP so callers (and tests) control the transport.
        ``secret`` is the site's credential from ``SecretStore``. The returned
        ``Post`` should have, where the platform provides them:

        - ``html``: the post body only (no site chrome); relative image URLs are fine
        - ``authors``: ``Author(name, url)``, with ``url`` the author's profile page
        - ``published_at``: ISO 8601 in the *site's* timezone, so dates match the site
        - ``feature_image`` / ``feature_image_alt`` / ``feature_image_caption`` (HTML)

        Raise on failure (e.g. ``requests.HTTPError``); a missing post stops the build.
        """


_REGISTRY: dict[str, type[Platform]] = {}


def register_platform(cls: type[Platform]) -> type[Platform]:
    """Class decorator: make ``cls`` available as ``--platform cls.name``."""
    _REGISTRY[cls.name] = cls
    return cls


def get_platform(name: str) -> Platform:
    """Return a new instance of the platform registered as ``name``."""
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise RuntimeError(f"Unknown platform: {name!r}. Registered: {sorted(_REGISTRY)}")
