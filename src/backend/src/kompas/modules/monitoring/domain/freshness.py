"""Data freshness (A12): "no active signals" is only meaningful next to how recent and complete the
data is. Shown on the doctor's patient list and card."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class Freshness:
    """``missed_days``: expected diary days without an entry in the window."""

    last_entry_on: date | None
    days_since_last: int | None
    missed_days: int
    window_days: int

    @property
    def complete(self) -> bool:
        """True when the window has at least one expected day and none is missing."""
        return self.window_days > 0 and self.missed_days == 0


def diary_freshness(entry_days: Sequence[date], today: date, window_days: int, since: date) -> Freshness:
    """Freshness of a daily diary over the last ``window_days`` days (counting only days on or
    after ``since``, the start of observation). Duplicate days count once."""
    days = {d for d in entry_days if d <= today}
    last = max(days, default=None)
    start = max(since, today - timedelta(days=window_days - 1))
    expected = [start + timedelta(days=i) for i in range((today - start).days + 1)] if start <= today else []
    missed = sum(1 for d in expected if d not in days)
    return Freshness(
        last_entry_on=last,
        days_since_last=(today - last).days if last else None,
        missed_days=missed,
        window_days=len(expected),
    )
