"""Upcoming birthdays and anniversaries for the tree overview dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from django.db.models import Prefetch
from django.urls import reverse

from .models import Event, Individual, MediaObject


@dataclass
class UpcomingItem:
    kind: Literal["birthday", "anniversary"]
    when: date
    days_until: int
    title: str
    subtitle: str
    url: str | None
    thumb_url: str | None
    is_today: bool


def _days_until_annual(month: int, day: int, today: date) -> tuple[date, int]:
    """Next calendar occurrence of month/day and days from *today*."""
    try:
        this_year = date(today.year, month, day)
    except ValueError:
        return today, 9999
    if this_year < today:
        try:
            nxt = date(today.year + 1, month, day)
        except ValueError:
            return today, 9999
    else:
        nxt = this_year
    return nxt, (nxt - today).days


def _media_thumb_url(media: MediaObject | None, tree_id: int, apply_privacy: bool) -> str | None:
    if not media or not media.file or not media.file.name:
        return None
    if not (media.is_image or media.is_pdf):
        return None
    if apply_privacy and (media.is_confidential or media.is_private):
        return None
    return reverse(
        "genview:media-thumb",
        kwargs={"tree_id": tree_id, "pk": media.pk, "size": "mini"},
    )


def _profile_media(individual: Individual) -> MediaObject | None:
    ordered = getattr(individual, "_ordered_media", None)
    if ordered is not None:
        return ordered[0] if ordered else None
    return individual.profile_image


def collect_upcoming_birthdays(
    tree_id: int,
    apply_privacy: bool,
    *,
    horizon_days: int = 30,
    today: date | None = None,
) -> list[UpcomingItem]:
    today = today or date.today()
    events = (
        Event.objects.filter(
            gedcom_tree_id=tree_id,
            event_type__tag="BIRT",
            individual__isnull=False,
            parsed_date__isnull=False,
        )
        .select_related("individual")
        .prefetch_related(
            Prefetch(
                "individual__media_objects",
                queryset=MediaObject.objects.order_by("-is_portrait", "id"),
                to_attr="_ordered_media",
            )
        )
    )
    items: list[UpcomingItem] = []
    seen: set[int] = set()

    for ev in events:
        ind = ev.individual
        if ind.pk in seen:
            continue
        seen.add(ind.pk)
        if ind.is_deceased:
            continue
        if apply_privacy and ind.is_confidential:
            continue
        when, days = _days_until_annual(ev.parsed_date.month, ev.parsed_date.day, today)
        if days > horizon_days:
            continue
        age = when.year - ev.parsed_date.year
        if (when.month, when.day) < (ev.parsed_date.month, ev.parsed_date.day):
            age -= 1
        items.append(
            UpcomingItem(
                kind="birthday",
                when=when,
                days_until=days,
                title=ind.full_name(),
                subtitle=f"wird {age}",
                url=ind.get_absolute_url(),
                thumb_url=_media_thumb_url(_profile_media(ind), tree_id, apply_privacy),
                is_today=days == 0,
            )
        )
    return items


def collect_upcoming_anniversaries(
    tree_id: int,
    apply_privacy: bool,
    *,
    horizon_days: int = 30,
    today: date | None = None,
) -> list[UpcomingItem]:
    today = today or date.today()
    events = (
        Event.objects.filter(
            gedcom_tree_id=tree_id,
            event_type__tag="MARR",
            family__isnull=False,
            parsed_date__isnull=False,
        )
        .select_related("family", "family__husband", "family__wife")
    )
    items: list[UpcomingItem] = []

    for ev in events:
        fam = ev.family
        if apply_privacy and fam.is_confidential:
            continue
        when, days = _days_until_annual(ev.parsed_date.month, ev.parsed_date.day, today)
        if days > horizon_days:
            continue
        years = when.year - ev.parsed_date.year
        if (when.month, when.day) < (ev.parsed_date.month, ev.parsed_date.day):
            years -= 1
        husband = fam.husband
        wife = fam.wife
        if husband and wife:
            title = f"{husband.full_name()} & {wife.full_name()}"
        else:
            title = str(fam)
        items.append(
            UpcomingItem(
                kind="anniversary",
                when=when,
                days_until=days,
                title=title,
                subtitle=f"{years} Jahre",
                url=fam.get_absolute_url(),
                thumb_url=None,
                is_today=days == 0,
            )
        )
    return items


def merge_upcoming(
    birthdays: list[UpcomingItem],
    anniversaries: list[UpcomingItem],
) -> tuple[list[UpcomingItem], list[UpcomingItem]]:
    """Return (today_items, upcoming_items) sorted by days_until."""
    all_items = birthdays + anniversaries
    today_items = sorted(
        [i for i in all_items if i.is_today],
        key=lambda i: (i.kind, i.title),
    )
    upcoming = sorted(
        [i for i in all_items if not i.is_today],
        key=lambda i: (i.days_until, i.kind, i.title),
    )
    return today_items, upcoming
