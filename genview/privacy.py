"""Living-person privacy windows shared by models and queryset filters.

A date is confidential when ``parsed + years > today``:
- birth (or christening/baptism): 110 years
- death: 30 years
- marriage: 80 years

People without a death date are still treated as deceased when there is
chronological evidence they cannot be alive (GEDCOM best practice / max lifespan):
- birth, christening, or baptism older than 110 years
- any dated event older than 110 years
- a death, burial, or cremation event, even without a date (``DEAT Y``)
Otherwise, missing dates fail closed (treated as living).
"""

from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

BIRTH_PRIVACY_YEARS = 110
DEATH_PRIVACY_YEARS = 30
MARRIAGE_PRIVACY_YEARS = 80
# Same horizon as birth: nobody that old is treated as living.
ASSUMED_DECEASED_YEARS = BIRTH_PRIVACY_YEARS

BIRTH_PROXY_TAGS = ("BIRT", "CHR", "BAPM")
DEATH_EVIDENCE_TAGS = ("DEAT", "BURI", "CREM")


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
