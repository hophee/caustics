"""Replaceable source of the current time, so time-dependent rules are testable.

The clock only knows the moment (aware UTC). Calendar dates always depend on a time zone — a dose
mark at 01:00 in Novosibirsk belongs to the local day, not the UTC one — so dates are derived
explicitly with ``local_date``.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    """Anything that can tell the current moment."""

    def now(self) -> datetime:
        """Return the current moment as an aware UTC datetime."""
        ...


class SystemClock:
    """Real wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock frozen at a given moment; tests and demos move it with ``advance``."""

    def __init__(self, moment: datetime) -> None:
        """Create a clock stopped at ``moment`` (must be timezone-aware)."""
        if moment.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._moment = moment.astimezone(UTC)

    def now(self) -> datetime:
        return self._moment

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward (or backward, with a negative delta)."""
        self._moment += delta


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    """Calendar date of ``moment`` in time zone ``tz``.

    Raises:
        ValueError: ``moment`` is naive.
    """
    if moment.tzinfo is None:
        raise ValueError("local_date needs a timezone-aware datetime")
    return moment.astimezone(tz).date()
