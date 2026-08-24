from django.db import migrations


FAMILY_EVENT_SPECS = (
    ("MARR", "Heirat", "FAM"),
    ("DIV", "Scheidung", "FAM"),
    ("CENS", "Volkszählung", "BOTH"),
)


def set_family_event_categories(apps, schema_editor):
    EventType = apps.get_model("genview", "EventType")
    for tag, name, category in FAMILY_EVENT_SPECS:
        obj, created = EventType.objects.get_or_create(
            tag=tag,
            defaults={"name": name, "category": category, "is_visible": True},
        )
        if not created and obj.category != category:
            obj.category = category
            obj.save(update_fields=["category"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("genview", "0016_tree_scoped_query_indexes"),
    ]

    operations = [
        migrations.RunPython(set_family_event_categories, noop_reverse),
    ]
