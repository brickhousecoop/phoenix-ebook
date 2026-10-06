"""Helpers for the canonical chapter HTML forms that more than one layer writes (docs/canonical-html.md)."""
from __future__ import annotations

# Words of a removed call-to-action link, kept for the reader and reported by the builder.
CALL_TO_ACTION = "data-call-to-action"
# A promotional image kept because it isn't at the end of the post (it may be content); reported.
CALL_TO_ACTION_BANNER = "data-call-to-action-banner"


def mark_call_to_action(soup, link) -> None:
    """Replace a call-to-action link by its words, marked for the build report."""
    span = soup.new_tag("span", attrs={CALL_TO_ACTION: link.get("href", "")})
    span.extend(list(link.contents))
    link.replace_with(span)
