from __future__ import annotations

from collections import defaultdict

from core.features import FEATURE_STORIES, feature_enabled

from .models import StoryPage


def visible_story_pages(tree_id: int, *, can_edit: bool):
    qs = StoryPage.objects.filter(gedcom_tree_id=tree_id)
    if not can_edit:
        qs = qs.filter(status=StoryPage.Status.PUBLISHED)
    return qs.order_by("tree_id", "lft")


def story_nav_tree(pages, current_pk=None) -> list[dict]:
    page_ids = {page.pk for page in pages}
    by_parent: dict[int | None, list] = defaultdict(list)
    for page in pages:
        parent_id = page.parent_id if page.parent_id in page_ids else None
        by_parent[parent_id].append(page)

    def sibling_key(page):
        order = page.sort_order if page.sort_order is not None else 0
        return (order, (page.title or "").casefold(), page.pk)

    def walk(parent_id):
        nodes = []
        siblings = sorted(by_parent.get(parent_id, []), key=sibling_key)
        for page in siblings:
            nodes.append(
                {
                    "page": page,
                    "children": walk(page.pk),
                    "current": page.pk == current_pk,
                }
            )
        return nodes

    return walk(None)


def pages_mentioning(obj, *, can_edit: bool) -> list[StoryPage]:
    if obj is None:
        return []
    from genview.models import Family, Individual, Place

    if isinstance(obj, Individual):
        filt = {"mentioned_individuals": obj}
    elif isinstance(obj, Family):
        filt = {"mentioned_families": obj}
    elif isinstance(obj, Place):
        filt = {"mentioned_places": obj}
    else:
        return []
    qs = StoryPage.objects.filter(gedcom_tree_id=obj.gedcom_tree_id, **filt)
    if not can_edit:
        qs = qs.filter(status=StoryPage.Status.PUBLISHED)
    return list(qs.order_by("sort_order", "title", "pk"))


def _rail_date_text(event) -> str:
    if event is None:
        return ""
    if event.parsed_date:
        return event.parsed_date.strftime("%d.%m.%Y")
    return (event.raw_date or "").strip()


def _rail_dates_line(events) -> str:
    birth = next((event for event in events if event.event_type.tag == "BIRT"), None)
    death = next((event for event in events if event.event_type.tag == "DEAT"), None)
    parts = []
    birth_text = _rail_date_text(birth)
    if birth_text:
        parts.append(f"* {birth_text}")
    death_text = _rail_date_text(death)
    if death is not None and death_text:
        parts.append(f"† {death_text}")
    return "  ".join(parts)


def prepare_mentioned_people(people, *, apply_privacy: bool):
    """Reload mentioned people with portraits, hiding private images when needed."""
    from django.db.models import Prefetch

    from genview.models import Event, Individual, MediaObject

    ids = [person.pk for person in people]
    if not ids:
        return []
    loaded = {
        person.pk: person
        for person in Individual.objects.filter(pk__in=ids).prefetch_related(
            Prefetch(
                "media_objects",
                queryset=MediaObject.objects.order_by(
                    "-is_portrait", "id"
                ).prefetch_related("individuals"),
            ),
            Prefetch("events", queryset=Event.objects.select_related("event_type")),
        )
    }
    ordered = []
    for person in people:
        fresh = loaded.get(person.pk, person)
        portrait = next(iter(fresh.media_objects.all()), None)
        if portrait is not None:
            hidden = apply_privacy and (
                portrait.is_confidential or portrait.is_private
            )
            usable = (
                portrait.file
                and portrait.file.name
                and portrait.is_image
                and not hidden
            )
            portrait = portrait if usable else None
        fresh.rail_portrait = portrait
        fresh.rail_dates = _rail_dates_line(list(fresh.events.all()))
        ordered.append(fresh)
    return ordered


def story_mentions_for_context(ctx, obj) -> None:
    if not feature_enabled(FEATURE_STORIES):
        ctx["story_mentions"] = []
        return
    ctx["story_mentions"] = pages_mentioning(obj, can_edit=bool(ctx.get("can_edit")))