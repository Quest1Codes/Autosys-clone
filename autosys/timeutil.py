"""Time helpers shared across the codebase."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Drop-in replacement for the deprecated ``datetime.utcnow()``.  The DB
    columns are naive ``DateTime``, so the tzinfo is stripped to keep
    comparisons with stored values working.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
