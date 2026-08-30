import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("genview", "0017_standard_family_event_types"),
    ]

    operations = [
        migrations.CreateModel(
            name="EntityTag",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=80, verbose_name="Name")),
                (
                    "description",
                    models.TextField(blank=True, verbose_name="Beschreibung"),
                ),
                (
                    "color",
                    models.CharField(
                        default="#6c757d",
                        help_text="Hex-Farbe, z. B. #198754",
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Farbe muss als Hex-Wert angegeben werden, z. B. #0d6efd.",
                                regex=r"^#[0-9A-Fa-f]{6}$",
                            )
                        ],
                        verbose_name="Farbe",
                    ),
                ),
                (
                    "gedcom_tree",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_tags",
                        to="genview.tree",
                    ),
                ),
            ],
            options={
                "verbose_name": "Markierung",
                "verbose_name_plural": "Markierungen",
                "ordering": ["name"],
            },
        ),
        migrations.AddConstraint(
            model_name="entitytag",
            constraint=models.UniqueConstraint(
                fields=("gedcom_tree", "name"),
                name="genview_entitytag_tree_name_uniq",
            ),
        ),
        migrations.AddField(
            model_name="individual",
            name="entity_tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="individuals",
                to="genview.entitytag",
                verbose_name="Markierungen",
            ),
        ),
        migrations.AddField(
            model_name="family",
            name="entity_tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="families",
                to="genview.entitytag",
                verbose_name="Markierungen",
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="entity_tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="events",
                to="genview.entitytag",
                verbose_name="Markierungen",
            ),
        ),
        migrations.AddField(
            model_name="place",
            name="entity_tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="places",
                to="genview.entitytag",
                verbose_name="Markierungen",
            ),
        ),
        migrations.AddField(
            model_name="mediaobject",
            name="entity_tags",
            field=models.ManyToManyField(
                blank=True,
                related_name="tagged_media",
                to="genview.entitytag",
                verbose_name="Markierungen",
            ),
        ),
    ]
