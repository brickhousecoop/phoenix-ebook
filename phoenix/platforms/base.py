from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from phoenix.models import Post


class Platform(ABC):
    name: str

    @abstractmethod
    def fetch_post(self, session: requests.Session, url: str, secret: str, slug: str) -> Post:
        """Fetch a single post identified by slug from the platform at url."""


_REGISTRY: dict[str, type[Platform]] = {}


def register_platform(cls: type[Platform]) -> type[Platform]:
    _REGISTRY[cls.name] = cls
    return cls


def get_platform(name: str) -> Platform:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise RuntimeError(f"Unknown platform: {name!r}. Registered: {sorted(_REGISTRY)}")
