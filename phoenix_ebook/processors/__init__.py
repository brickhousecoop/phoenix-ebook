from phoenix_ebook.processors.base import (
    GenericProcessor,
    HtmlProcessor,
    get_processor,
    register_processor,
    register_site_files,
)
from phoenix_ebook.processors import flaminghydra  # noqa: F401 — side-effect registration

register_site_files()  # after the Python processors: a blog's file doesn't replace its Python

__all__ = [
    "GenericProcessor",
    "HtmlProcessor",
    "get_processor",
    "register_processor",
]
