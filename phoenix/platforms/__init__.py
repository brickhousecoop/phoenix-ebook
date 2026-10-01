from phoenix.platforms.base import Platform, get_platform, register_platform
from phoenix.platforms import ghost  # noqa: F401 — side-effect registration

__all__ = ["Platform", "get_platform", "register_platform"]
