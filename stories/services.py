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

    def walk(parent_id):
        nodes = []
        for page in by_parent.get(parent_id, []):
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
    return list(qs.order_by("title"))


def story_mentions_for_context(ctx, obj) -> None:
    if not feature_enabled(FEATURE_STORIES):
        ctx["story_mentions"] = []
        return
    ctx["story_mentions"] = pages_mentioning(obj, can_edit=bool(ctx.get("can_edit")))