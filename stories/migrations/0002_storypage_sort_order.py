from collections import defaultdict

from django.db import migrations, models


def assign_sort_order_from_current_tree(apps, schema_editor):
    StoryPage = apps.get_model("stories", "StoryPage")
    pages = list(StoryPage.objects.order_by("tree_id", "lft", "pk"))
    groups = defaultdict(list)
    for page in pages:
        groups[(page.gedcom_tree_id, page.parent_id)].append(page)
    updates = []
    for siblings in groups.values():
        for index, page in enumerate(siblings, start=1):
            page.sort_order = index * 10
            updates.append(page)
    if updates:
        StoryPage.objects.bulk_update(updates, ["sort_order"])


def clear_sort_order(apps, schema_editor):
    StoryPage = apps.get_model("stories", "StoryPage")
    StoryPage.objects.update(sort_order=None)


class Migration(migrations.Migration):
    dependencies = [
        ("stories", "0001_storypage"),
    ]

    operations = [
        migrations.AddField(
            model_name="storypage",
            name="sort_order",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Kleine Zahlen stehen weiter oben. Die Reihenfolge gilt "
                    "unter Seiten derselben Ebene. Leer lassen, um die Seite "
                    "am Ende einzufügen."
                ),
                null=True,
                verbose_name="Reihenfolge",
            ),
        ),
        migrations.RunPython(
            assign_sort_order_from_current_tree, clear_sort_order
        ),
    ]
