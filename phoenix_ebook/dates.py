"""Dates as books show them."""
from __future__ import annotations

from datetime import datetime

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_date(timestamp: str | None) -> str | None:
    """'2026-08-24T20:04:00-04:00' -> '24 Aug 2026' (the date as written, no tz shift)."""
    if not timestamp:
        return None
    try:
        d = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{d.day:02d} {_MONTHS[d.month - 1]} {d.year}"
