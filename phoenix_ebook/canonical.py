"""Helpers for the canonical chapter HTML forms that more than one layer writes (docs/canonical-html.md)."""
from __future__ import annotations

# On the post's feature image <figure>, which the builder puts first in the post before processing.
FEATURE_IMAGE = "data-feature-image"

# Words of a removed call-to-action link, kept for the reader and reported by the builder.
CALL_TO_ACTION = "data-call-to-action"
# A promotional image kept because it isn't at the end of the post (it may be content); reported.
CALL_TO_ACTION_BANNER = "data-call-to-action-banner"
# An empty marker left where a closing appeal was removed (#30): the removed words, and its link in
# CALL_TO_ACTION_REMOVED_URL. The builder reports it and removes the marker.
CALL_TO_ACTION_REMOVED = "data-call-to-action-removed"
CALL_TO_ACTION_REMOVED_URL = "data-call-to-action-removed-url"


def mark_call_to_action(soup, link) -> None:
    """Replace a call-to-action link by its words, marked for the build report."""
    span = soup.new_tag("span", attrs={CALL_TO_ACTION: link.get("href", "")})
    span.extend(list(link.contents))
    link.replace_with(span)

# ---- Link cards (docs/canonical-html.md): embedded videos, audio, posts and bookmarks ----

CARD_CLASS = "card"
# An image with no meaning of its own (a card's thumbnail): no alt text wanted, none reported.
DECORATIVE = "data-decorative"
# On a card's thumbnail <img> without src: an oEmbed URL the builder asks for the thumbnail's URL.
THUMBNAIL_LOOKUP = "data-thumbnail-lookup"
# On a card's thumbnail <img>: a second URL to try if src can't be downloaded.
THUMBNAIL_FALLBACK = "data-fallback-src"
# On text inside a card with a thumbnail lookup: a str.format template filled from the
# lookup's answer (e.g. "{author_name} (@handle)"); the element's text stays if it fails.
OEMBED_TEXT = "data-oembed-text"
# An embed left out of the book (e.g. a form); the value says what it was. Reported.
EMBED_REMOVED = "data-embed-removed"
# On a card made from an embed nobody recognised; the value is its source URL. Reported.
EMBED_UNKNOWN = "data-embed-unknown"
EMBED_SRC = "data-embed-src"

# ---- Changed links (#22): every link the build repairs, unlinks or removes is reported ----

# On a repaired <a>: its original href, and why it changed.
LINK_REPAIRED = "data-link-repaired"
LINK_REASON = "data-link-reason"
# On a <span> holding the words of a link that pointed nowhere (the link is gone): its href.
LINK_UNLINKED = "data-link-unlinked"
# An empty <span> where a link with no text was: its href.
LINK_REMOVED = "data-link-removed"
