from phoenix_ebook.platforms.base import Platform, get_platform, register_platform
from phoenix_ebook.platforms import ghost  # noqa: F401 — side-effect registration

__all__ = ["Platform", "get_platform", "register_platform"]
