"""Errors a user can cause and fix. Each message says what went wrong and what to do.

Callers (the CLI, the future web UI) catch PhoenixError and show the message;
anything else escaping is a bug.
"""
from __future__ import annotations

import json


class PhoenixError(Exception):
    """Base class for errors with a message meant for the user."""

    field: str | None = None  # the BookSpec field this concerns, if any (set by callers that know)


class InvalidBookSpec(PhoenixError, ValueError):
    """The BookSpec can't be built as given (the message says why)."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class MissingContentFile(InvalidBookSpec):
    """A content-file option names a path that doesn't exist."""


class ManifestError(PhoenixError):
    """The TOML manifest can't be read or is missing required settings."""


class OutputError(PhoenixError):
    """The EPUB can't be written where requested."""


class MissingSecret(PhoenixError):
    """No credential is stored for a platform and site."""


class AuthError(PhoenixError):
    """The platform rejected the credential, or the credential is malformed."""


class SourceUnreachable(PhoenixError):
    """The platform couldn't be reached or didn't answer like the expected API."""


class PostNotFound(PhoenixError):
    """One or more posts don't exist on the site."""

    def __init__(self, site: str, slugs: list[str]):
        self.site = site
        self.slugs = list(slugs)
        quoted = ", ".join(f'"{s}"' for s in self.slugs)
        noun = "post" if len(self.slugs) == 1 else "posts"
        super().__init__(f"{len(self.slugs)} {noun} not found on {site}: {quoted}. "
                         "Check the slugs (the last part of each post's URL).")


def explain_response(resp) -> str | None:
    """A short, human-readable explanation from an HTTP error response's body, if it has one.

    JSON bodies: the first error message (Ghost: errors[0].message / context).
    Plain text (e.g. a firewall's "Approval required for …"): the first two lines.
    HTML pages: None (they're noise).
    """
    content_type = (resp.headers.get("Content-Type") or "").lower()
    body = resp.content or b""
    text = body.decode("utf-8", "replace").strip()
    if not text or "html" in content_type or text.lstrip().lower().startswith(("<!doctype", "<html")):
        return None
    if "json" in content_type or text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors and isinstance(errors, list) and isinstance(errors[0], dict):
            parts = [errors[0].get("message"), errors[0].get("context")]
            return " ".join(p for p in parts if p)[:300] or None
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    keep = lines[:2]
    if len(lines) > 2 and keep[-1].endswith(":"):  # "Review and respond with:" -> include what follows
        keep.append(lines[2])
    return " ".join(keep)[:300]
