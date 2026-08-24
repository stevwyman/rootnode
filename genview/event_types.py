"""Canonical GEDCOM event types used on family records."""

from genview.models import EventType

# Family-owned tags, plus CENS which GEDCOM allows on both FAM and INDI.
STANDARD_FAMILY_EVENT_TYPES = (
    ("MARR", "Heirat", EventType.Category.FAMILY),
    ("DIV", "Scheidung", EventType.Category.FAMILY),
    ("CENS", "Volkszählung", EventType.Category.BOTH),
)


def ensure_standard_family_event_types():
    """Create MARR/DIV/CENS if missing and keep their GEDCOM categories."""
    for tag, name, category in STANDARD_FAMILY_EVENT_TYPES:
        obj, created = EventType.objects.get_or_create(
            tag=tag,
            defaults={"name": name, "category": category, "is_visible": True},
        )
        if not created and obj.category != category:
            obj.category = category
            obj.save(update_fields=["category"])
    return EventType.objects.filter(
        tag__in=[tag for tag, _, _ in STANDARD_FAMILY_EVENT_TYPES]
    )


def ensure_birth_event_type():
    """BIRT stays an individual event; families record it on children."""
    obj, created = EventType.objects.get_or_create(
        tag="BIRT",
        defaults={
            "name": "Geburt",
            "category": EventType.Category.INDIVIDUAL,
            "is_visible": True,
        },
    )
    if not created and obj.category != EventType.Category.INDIVIDUAL:
        obj.category = EventType.Category.INDIVIDUAL
        obj.save(update_fields=["category"])
    return obj
