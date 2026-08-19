"""Upcoming birthdays and anniversaries for the tree overview dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from django.db.models import Exists, OuterRef, Prefetch
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


def _horizon_months(today: date, horizon_days: int) -> set[int]:
    months = set()
    cursor = today
    end = today + timedelta(days=horizon_days)
    while cursor <= end:
        months.add(cursor.month)
        cursor += timedelta(days=1)
    return months


def _media_thumb_url(media: MediaObject | None, tree_id: int, apply_privacy: bool) -> str | None:
    if not media or not media.file or not media.file.name:
        return None
    if not (media.is_image or media.is_pdf):
        return None
    if apply_privacy and media.is_private:
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


def _profile_thumbs(
    tree_id: int,
    individual_ids: list[int],
    apply_privacy: bool,
    public_ids=None,
) -> dict[int, str | None]:
    if not individual_ids:
        return {}
    from .mixins import apply_privacy_to_media_qs

    media_qs = MediaObject.objects.filter(gedcom_tree_id=tree_id).order_by(
        "-is_portrait", "id"
    )
    if apply_privacy:
        media_qs = apply_privacy_to_media_qs(
            media_qs, True, tree_id, public_ids=public_ids
        )
    people = Individual.objects.filter(pk__in=individual_ids).prefetch_related(
        Prefetch(
            "media_objects",
            queryset=media_qs,
            to_attr="_ordered_media",
        )
    )
    return {
        person.pk: _media_thumb_url(_profile_media(person), tree_id, apply_privacy)
        for person in people
    }


def collect_upcoming_birthdays(
    tree_id: int,
    apply_privacy: bool,
    *,
    horizon_days: int = 30,
    today: date | None = None,
    public_ids=None,
) -> list[UpcomingItem]:
    today = today or date.today()
    has_death = Exists(
        Event.objects.filter(
            gedcom_tree_id=tree_id,
            individual_id=OuterRef("individual_id"),
            event_type__tag="DEAT",
        )
    )
    events = (
        Event.objects.filter(
            gedcom_tree_id=tree_id,
            event_type__tag="BIRT",
            individual__isnull=False,
            parsed_date__isnull=False,
            parsed_date__month__in=_horizon_months(today, horizon_days),
        )
        .annotate(_deceased=has_death)
        .filter(_deceased=False)
        .select_related("individual")
        .prefetch_related(Individual.titl_events_prefetch("individual__events"))
    )
    if apply_privacy:
        if public_ids is None:
            from .mixins import public_individual_pks

            public_ids = public_individual_pks(tree_id)
        events = events.filter(individual_id__in=public_ids)

    hits: list[tuple[Individual, Event, date, int, int]] = []
    seen: set[int] = set()
    for ev in events:
        ind = ev.individual
        if ind.pk in seen:
            continue
        seen.add(ind.pk)
        when, days = _days_until_annual(ev.parsed_date.month, ev.parsed_date.day, today)
        if days > horizon_days:
            continue
        age = when.year - ev.parsed_date.year
        if (when.month, when.day) < (ev.parsed_date.month, ev.parsed_date.day):
            age -= 1
        hits.append((ind, ev, when, days, age))

    thumbs = _profile_thumbs(
        tree_id, [ind.pk for ind, *_ in hits], apply_privacy, public_ids
    )
    return [
        UpcomingItem(
            kind="birthday",
            when=when,
            days_until=days,
            title=ind.full_name(),
            subtitle=f"wird {age}",
            url=ind.get_absolute_url(),
            thumb_url=thumbs.get(ind.pk),
            is_today=days == 0,
        )
        for ind, ev, when, days, age in hits
    ]


def collect_upcoming_anniversaries(
    tree_id: int,
    apply_privacy: bool,
    *,
    horizon_days: int = 30,
    today: date | None = None,
    public_ids=None,
    public_family_ids=None,
) -> list[UpcomingItem]:
    today = today or date.today()
    events = Event.objects.filter(
        gedcom_tree_id=tree_id,
        event_type__tag="MARR",
        family__isnull=False,
        parsed_date__isnull=False,
        parsed_date__month__in=_horizon_months(today, horizon_days),
    ).select_related("family", "family__husband", "family__wife").prefetch_related(
        Individual.titl_events_prefetch("family__husband__events"),
        Individual.titl_events_prefetch("family__wife__events"),
    )
    if apply_privacy:
        if public_family_ids is None:
            from .mixins import apply_privacy_to_family_qs
            from .models import Family

            public_family_ids = apply_privacy_to_family_qs(
                Family.objects.filter(gedcom_tree_id=tree_id),
                True,
                tree_id,
                public_ids=public_ids,
            ).values("pk")
        events = events.filter(family_id__in=public_family_ids)

    items: list[UpcomingItem] = []
    for ev in events:
        fam = ev.family
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
