from __future__ import annotations

from bs4 import BeautifulSoup

from phoenix.models import Post


class HtmlProcessor:
    name: str = "generic"

    def display_title(self, post: Post) -> str:
        return post.title

    def clean(self, soup: BeautifulSoup, post: Post) -> None:
        for tag in soup.find_all(["script", "iframe", "style"]):
            tag.decompose()


class GenericProcessor(HtmlProcessor):
    name = "generic"


_REGISTRY: dict[str, type[HtmlProcessor]] = {}


def register_processor(cls: type[HtmlProcessor]) -> type[HtmlProcessor]:
    _REGISTRY[cls.name] = cls
    return cls


def get_processor(name: str) -> HtmlProcessor:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise RuntimeError(f"Unknown processor: {name!r}. Registered: {sorted(_REGISTRY)}")


register_processor(GenericProcessor)
