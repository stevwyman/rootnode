"""Living-person privacy windows shared by models and queryset filters.

A date is confidential when ``parsed + years > today``:
- birth: 110 years
- death: 80 years
- marriage: 60 years
"""

from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

BIRTH_PRIVACY_YEARS = 110
DEATH_PRIVACY_YEARS = 80
MARRIAGE_PRIVACY_YEARS = 60


def add_years(parsed: date, years: int) -> date:
    return parsed + relativedelta(years=years)


def cutoff_date(years: int, today: date | None = None) -> date:
    """Dates *strictly after* this cutoff fall inside the privacy window."""
    today = today or date.today()
    return today - relativedelta(years=years)


def is_within_privacy_window(
    parsed: date | None, years: int, today: date | None = None
) -> bool:
    """True if ``parsed + years`` is still after today (too recent to publish)."""
    if parsed is None:
        return False
    today = today or date.today()
    return add_years(parsed, years) > today
