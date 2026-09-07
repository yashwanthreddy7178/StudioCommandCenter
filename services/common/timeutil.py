"""UTC timestamp helpers shared across services.

Two representations exist in this system, and the split is deliberate:

* SQLAlchemy columns store **naive** datetimes holding UTC. impact-engine runs
  on SQLite in tests and Postgres in production, and the two dialects disagree
  about what an offset-aware column round-trips to, so the storage layer is kept
  offset-free and the offset is applied on the way out.
* Everything that leaves a process -- Pydantic models, JSON payloads -- carries
  an **aware** UTC datetime, which Pydantic serializes with a "+00:00" suffix.

The suffix is the whole point. JavaScript parses a bare "2026-09-06T00:52:25"
as local time, so the browser rendered every figure shifted by the viewer's own
offset while labelling it UTC: four hours out on a US East Coast machine, and a
different number for every viewer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Returns the current time as an aware UTC datetime.

    Replaces `datetime.utcnow()`, which despite its name returns a naive value
    and is deprecated from Python 3.12 onwards.
    """
    return datetime.now(timezone.utc)


def naive_utc_now() -> datetime:
    """Returns the current time as a naive UTC datetime, for a DateTime column."""
    return utc_now().replace(tzinfo=None)


def to_utc(value: datetime) -> datetime:
    """Reads a datetime as UTC: labels a naive value, converts an aware one."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_naive_utc(value: datetime) -> datetime:
    """Converts to UTC and drops the offset, for storing in a DateTime column."""
    return to_utc(value).replace(tzinfo=None)


def opt_to_utc(value: Optional[datetime]) -> Optional[datetime]:
    """`to_utc` for a value that may be absent."""
    return None if value is None else to_utc(value)
