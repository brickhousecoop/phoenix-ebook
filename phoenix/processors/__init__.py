from phoenix.processors.base import (
    GenericProcessor,
    HtmlProcessor,
    get_processor,
    register_processor,
)
from phoenix.processors import flaminghydra  # noqa: F401 — side-effect registration

__all__ = [
    "GenericProcessor",
    "HtmlProcessor",
    "get_processor",
    "register_processor",
]
